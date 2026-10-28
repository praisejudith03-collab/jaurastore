import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const src = readFileSync('js/admin.js', 'utf8');
const start = src.indexOf('function uploadErrorMessage(');
const end = src.indexOf('function readableBytes(', start);
const context = vm.createContext({ document: { createElement() {
  return { className: '', hidden: false, textContent: '', attributes: {},
    setAttribute(k, v) { this.attributes[k] = v; },
    classList: { contains(name) { return name === 'upload-error-banner'; } } };
} } });
vm.runInContext(src.slice(start, end), context);
const parent = { insertBefore(node, anchor) { anchor.previousElementSibling = node; } };
const anchor = { parentNode: parent, previousElementSibling: null };
context.showUploadError(anchor, 'Unsupported <file>');
const banner = anchor.previousElementSibling;
if (banner.className !== 'upload-error-banner' || banner.attributes.role !== 'alert' ||
    banner.textContent !== 'Unsupported <file>' || banner.hidden) throw new Error('invalid banner');
context.showUploadError(anchor, context.uploadErrorMessage({status: 413}, 'fallback'));
if (anchor.previousElementSibling !== banner || !banner.textContent.includes('30 MB')) throw new Error('413 not explained');
context.showUploadError(anchor, '');
if (!banner.hidden) throw new Error('banner not cleared');
console.log('upload error banner checks passed');
