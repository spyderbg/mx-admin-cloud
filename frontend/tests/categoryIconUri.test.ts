import assert from 'node:assert/strict'
import { test } from 'node:test'
import { categoryIconUri } from '../src/utils/categoryIds.ts'

test('the icon is the image named after the category folder, not the first image', () => {
  const images = [
    { id: 'at_school_01', file_uri: '/content/at_school/at_school_01.png' },
    { id: 'at_school', file_uri: '/content/at_school/at_school.jpg' },
  ]
  assert.equal(categoryIconUri('C_AtSchool', images), '/content/at_school/at_school.jpg')
})

test('no icon among the images means null, even when extra images remain', () => {
  assert.equal(categoryIconUri('C_AtSchool', [{ id: 'at_school_01', file_uri: '/content/at_school/at_school_01.png' }]), null)
  assert.equal(categoryIconUri('C_AtSchool', []), null)
  assert.equal(categoryIconUri('C_AtSchool', undefined), null)
  assert.equal(categoryIconUri('C_AtSchool', [{ id: 'at_school', file_uri: null }]), null)
})
