# Exchange-rate save scope fix

The Store Settings exchange-rate control now calls the shared network client
from within its own lexical scope. Previously, `bindExchangeRate()` referenced
an `api()` helper that was declared locally inside unrelated functions: loading
silently fell through its catch, while saving showed `api is not defined`.

Added two regression guards:

- an Acorn-based shipped-JavaScript scope scan that detects calls to a helper
  declared only in another function;
- a harness that extracts and executes the real `bindExchangeRate()` against a
  fake settings form, verifying both the initial load and the save request.

CI installs Acorn alongside jsdom and treats missing dependencies as a failure.
The shared browser asset token is now **201** so clients fetch the corrected
`js/admin.js` bundle.
