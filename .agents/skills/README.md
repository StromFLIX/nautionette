# Repository skills

These skills are vendored for coding agents working in this repository. Compatible
agents discover `.agents/skills/` automatically. In Pi, use `/skill:<name>` to
load one explicitly (for example, `/skill:emil-design-eng` or
`/skill:design-taste-frontend`). They are not application runtime dependencies.

## Sources

| Skills | Upstream | Pinned revision | License |
| --- | --- | --- | --- |
| `frontend-design` | [anthropics/skills](https://github.com/anthropics/skills) | `2c5958ecac7cee2b583967a2c4981d0f4a75c579` | Apache 2.0; see its `LICENSE.txt` |
| All 18 Emil Kowalski skills listed below | [emilkowalski/skills](https://github.com/emilkowalski/skills) (formerly `emilkowalski/skill`) | `5206a950b715e0cabe7043ea0b2304332b91494e` | MIT; `LICENSE` copied into each skill |
| `design-taste-frontend` | [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill), linked from [tasteskill.dev](https://www.tasteskill.dev/) | `f6b76575f949707b4e3c37cd86cb44b0b98643e8` | MIT; see its `LICENSE` |

Emil Kowalski's collection includes:

- `animate`
- `animate-expo`
- `animation-performance`
- `animation-vocabulary`
- `apple-design`
- `ask-sonner`
- `break-ui`
- `choreograph`
- `emil-design-eng`
- `find-animation-opportunities`
- `gestures`
- `improve-animations`
- `mobile-native`
- `pick-ui-library`
- `prototype`
- `remove-animations`
- `review-animations`
- `write-swift`

## Supporting files and updates

Upstream skill files are unchanged. Each skill's complete directory is included,
not just `SKILL.md`, so relative references, scripts, libraries and assets work.
Taste Skill's upstream folder `skills/taste-skill` is installed as
`design-taste-frontend` to match its declared skill name and CLI install name.
Its photograph source and license information remains in its asset metadata.

Taste Skill's tools require Node 22+ and Chrome, Edge or Chromium; `ffmpeg` is
optional. Its frontmatter includes a Claude-specific Stop hook; other agents
should run the documented checks explicitly rather than assume that hook runs.
No global installation or agent-specific symlinks are needed.

To update, copy the complete skill directories from a pinned upstream revision,
retain the corresponding licenses, update the revision above, and verify that
all skill names are unique and supporting files still match upstream. Keep
changes to vendored content separate from local integration documentation.
