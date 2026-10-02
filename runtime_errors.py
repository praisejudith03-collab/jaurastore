"""Process-wide diagnostics for unhandled Python runtime errors.

This is a Flask/WSGI application, not a Node process: Python has no global
``uncaughtException`` / ``unhandledRejection`` event pair. Their closest
safe equivalents are ``sys.excepthook``, ``threading.excepthook``,
``sys.unraisablehook`` and an asyncio loop exception handler when a loop is
actually running. These hooks record the failure and then chain to Python's
default handler; they deliberately do not swallow fatal errors. Gunicorn and
Render remain responsible for recycling an unhealthy worker/instance.
"""
import asyncio
import sys
import threading

from config import Config

_lock = threading.Lock()
_installed = False
_logger = None
_original_sys_hook = None
_original_thread_hook = None
_original_unraisable_hook = None


def _log_exception(job, exc_type, exc_value, exc_tb):
    logger = _logger
    if logger is not None:
        try:
            logger.error("%s: %s", job, exc_value,
                         exc_info=(exc_type, exc_value, exc_tb))
        except Exception:
            pass
    try:
        import observability
        observability.record_failure(job, exc_value, logger=logger)
    except Exception:
        # Error reporting must never replace the original failure.
        pass


def install_asyncio_loop_handler(loop, logger=None):
    """Install diagnostics on one concrete asyncio loop (if a caller has one).

    The current Gunicorn worker is synchronous and has no persistent loop, but
    this helper keeps the equivalent of ``unhandledRejection`` covered if an
    async subsystem is introduced later.
    """
    if loop is None:
        return False
    previous = loop.get_exception_handler()

    def handler(active_loop, context):
        exc = context.get("exception")
        if exc is None:
            exc = RuntimeError(str(context.get("message") or "unhandled asyncio exception"))
        active_logger = logger or _logger
        try:
            if active_logger is not None:
                active_logger.error("runtime.unhandled_asyncio_exception: %s", exc,
                                    exc_info=(type(exc), exc, exc.__traceback__))
            import observability
            observability.record_failure("runtime.unhandled_asyncio_exception",
                                         exc, logger=active_logger)
        except Exception:
            pass
        if previous is not None:
            previous(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
    return True


def install(logger=None, force=False):
    """Install one set of global handlers, only on a real runtime by default."""
    global _installed, _logger, _original_sys_hook
    global _original_thread_hook, _original_unraisable_hook
    if Config.ENV == "testing" and not force:
        return False
    with _lock:
        if logger is not None:
            _logger = logger
        if _installed:
            return False
        _original_sys_hook = sys.excepthook
        _original_thread_hook = getattr(threading, "excepthook", None)
        _original_unraisable_hook = getattr(sys, "unraisablehook", None)

        def sys_hook(exc_type, exc_value, exc_tb):
            _log_exception("runtime.uncaught_exception", exc_type, exc_value, exc_tb)
            if _original_sys_hook:
                _original_sys_hook(exc_type, exc_value, exc_tb)

        def thread_hook(args):
            _log_exception("runtime.unhandled_thread_exception",
                           args.exc_type, args.exc_value, args.exc_traceback)
            if _original_thread_hook:
                _original_thread_hook(args)

        def unraisable_hook(args):
            exc_value = getattr(args, "exc_value", None) or RuntimeError(
                str(getattr(args, "err_msg", "unraisable runtime exception")))
            _log_exception("runtime.unraisable_exception", type(exc_value), exc_value,
                           getattr(args, "exc_traceback", None))
            if _original_unraisable_hook:
                _original_unraisable_hook(args)

        sys.excepthook = sys_hook
        if _original_thread_hook is not None:
            threading.excepthook = thread_hook
        if _original_unraisable_hook is not None:
            sys.unraisablehook = unraisable_hook
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            install_asyncio_loop_handler(loop, _logger)
        _installed = True
        return True
