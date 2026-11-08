import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.HTMLElement=class{};
globalThis.customElements={get:()=>true};
const {safeLink}=await import('../web/profile-card.js');
test('card links reject executable URLs and embedded credentials',()=>{
  assert.equal(safeLink('javascript:alert(1)'),null);
  assert.equal(safeLink('https://user:pass@example.test'),null);
  assert.equal(safeLink('http://example.test'),null);
  assert.equal(safeLink('https://example.test/review'),'https://example.test/review');
});
