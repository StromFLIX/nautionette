import assert from 'node:assert/strict'
import test from 'node:test'
import { buildChangeTree } from './projectChanges.js'

test('groups changed files by repository directories, sorted folders first', () => {
  const files = [
    { path: 'z.txt', status: 'deleted' },
    { path: 'src/z.js', additions: 2 },
    { path: 'src/components/View.vue', previous_path: 'old.vue', status: 'renamed' },
    { path: 'src/a.js', binary: true },
    { path: 'README.md', additions: 1 }
  ]
  const tree = buildChangeTree(files)
  assert.deepEqual(tree.map((node) => node.name), ['src', 'README.md', 'z.txt'])
  assert.deepEqual(tree[0].children.map((node) => node.name), ['components', 'a.js', 'z.js'])
  assert.deepEqual(tree[0].children[0].children[0], { ...files[2], name: 'View.vue' })
  assert.equal(tree[0].children[1].binary, true)
  assert.equal(files[2].name, undefined)
})

test('keeps a deleted file and the directory replacing it as separate entries', () => {
  for (const files of [
    [{ path: 'src', status: 'deleted' }, { path: 'src/new.js', status: 'untracked' }],
    [{ path: 'src/old.js', status: 'deleted' }, { path: 'src', status: 'untracked' }]
  ]) {
    const tree = buildChangeTree(files)
    assert.equal(tree.length, 2)
    assert.equal(tree[0].children.length, 1)
    assert.equal(tree[1].path, 'src')
    assert.equal(tree[1].children, undefined)
  }
})

test('handles empty results, special names and duplicate basenames in different folders', () => {
  assert.deepEqual(buildChangeTree(), [])
  const tree = buildChangeTree([
    { path: '__proto__/a.txt' }, { path: 'constructor/a.txt' }, { path: 'space dir/tab\tfile' }
  ])
  assert.equal(tree.length, 3)
  assert.deepEqual(tree.flatMap((node) => node.children.map((child) => child.path)).sort(),
    ['__proto__/a.txt', 'constructor/a.txt', 'space dir/tab\tfile'])
})
