import assert from 'node:assert/strict'
import { test } from 'node:test'
import { cacheBustedUri } from '../src/utils/cacheBustedUri.ts'

test('cache versions precede fragments and use the separator from the URI before the fragment', () => {
  for (const [uri, expected] of [
    ['/img.png#preview', '/img.png?v=2#preview'],
    ['/img.png?lang=bg#preview', '/img.png?lang=bg&v=2#preview'],
    ['/img.png#preview?zoom=1', '/img.png?v=2#preview?zoom=1'],
    ['/img.png#', '/img.png?v=2#'],
    ['/img.png#preview#detail', '/img.png?v=2#preview#detail'],
  ]) {
    assert.equal(cacheBustedUri(uri, 2), expected)
  }
})

test('ordinary URIs retain their existing query separator behavior', () => {
  assert.equal(cacheBustedUri('/img.png', 2), '/img.png?v=2')
  assert.equal(cacheBustedUri('/img.png?lang=bg', 2), '/img.png?lang=bg&v=2')
})

test('empty URIs, browser previews, and falsy versions retain their early returns', () => {
  for (const uri of ['', null, undefined]) {
    assert.equal(cacheBustedUri(uri, 2), '')
  }
  for (const uri of ['blob:http://localhost/preview#detail', 'data:image/png;base64,AAAA#preview']) {
    assert.equal(cacheBustedUri(uri, 2), uri)
  }
  for (const uri of ['/img.png', '/img.png#preview', '/img.png?lang=bg#preview']) {
    for (const version of [0, NaN]) {
      assert.equal(cacheBustedUri(uri, version), uri)
    }
  }
})
