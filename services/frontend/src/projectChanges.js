/** Build a repository-relative tree containing only changed files and their parents. */
export function buildChangeTree (files = []) {
  const root = { children: new Map() }
  for (const file of files) {
    const parts = file.path.split('/')
    let parent = root
    parts.forEach((name, index) => {
      const path = parts.slice(0, index + 1).join('/')
      if (index === parts.length - 1) {
        parent.children.set(`file:${name}`, { ...file, name })
      } else {
        const key = `directory:${name}`
        if (!parent.children.has(key)) parent.children.set(key, { name, path, children: new Map() })
        parent = parent.children.get(key)
      }
    })
  }
  const ordered = (children) => [...children.values()]
    .sort((a, b) => Number(!!b.children) - Number(!!a.children) || a.name.localeCompare(b.name))
    .map((node) => node.children ? { ...node, children: ordered(node.children) } : node)
  return ordered(root.children)
}
