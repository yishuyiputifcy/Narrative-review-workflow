# Narrative Review plugin

This repository is a GitHub-distributable Codex marketplace containing one skills-only plugin, `narrative-review`. Installation does not run any workflow, download dependencies, or modify a paper project.

## Install

Add this marketplace:

```powershell
codex plugin marketplace add yishuyiputifcy/Narrative-review-workflow --ref main
```

Then restart the ChatGPT desktop app, open the Plugins Directory, select the **Narrative Review Toolkit** source, and install **Narrative Review**. A ChatGPT workspace administrator may instead import https://github.com/yishuyiputifcy/Narrative-review-workflow from **Workspace settings > Plugins > Add > Import marketplace** with an empty Path when this repository is the marketplace root.

## Included skills

| Skill                     | Codex invocation                              | Notes                                                                                                                                                     |
| ------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Narrative review workflow | `$narrative-review:narrative-review-workflow` | Explicit-only. The current message must contain this exact name and ask to use it. Discussion, examples, and historical invocations do not authorize use. |
| Academic Research Suite   | `$narrative-review:academic-research-suite`   | Bundled ARS-Codex router and its vendored resources. Its own natural-language and alias routing rules are retained.                                       |
| Humanizer                 | `$narrative-review:humanizer`                 | Bundled prose-rewrite skill. Its own natural-language invocation rules are retained.                                                                      |

The main workflow keeps `policy.allow_implicit_invocation: false`. This blocks implicit platform selection for that skill; it is a behavior policy, not filesystem isolation. Installing the plugin does not itself invoke any skill.

## External dependency

The OpenAI `documents` skill is not included. Its license prohibits extraction and redistribution. Word creation and render/verification steps therefore require the separately available OpenAI Documents plugin and its `$documents:documents` skill. All non-Word stages remain packaged without it.

## Examples

Read only the main entry contract without running a workflow script:

```text
请使用 $narrative-review:narrative-review-workflow，只读取入口并说明调用规则；不要运行脚本或执行论文工作单元。
```

Use a bundled helper independently:

```text
请使用 $narrative-review:academic-research-suite，以 ars-outline 模式整理我提供的研究问题。
```

```text
请使用 $narrative-review:humanizer，仅润色下面的文字，不改变事实、数字和引用。
```

## Runtime

- A supported Codex or ChatGPT plugin host.
- Python 3.10 or newer for `workflow_state.py`; it uses only the Python standard library.
- Browser, filesystem, and document tooling depend on the active Codex environment and user authorization.
- ARS contains optional scripts with additional requirements documented inside `skills/academic-research-suite/ars/`; no dependency is downloaded automatically.
- The external OpenAI Documents plugin is required only for Word generation and visual verification.

## Licensing and provenance

- `academic-research-suite` vendors Academic Research Skills from `Imbad0202/academic-research-skills` and Experiment Agent at the commits recorded in its `manifest.json`. The vendored ARS content is CC BY-NC 4.0; its `LICENSE` and `NOTICE.md` are retained.
- `humanizer` comes from `blader/humanizer`, copyright Siqi Chen, under the MIT License; its `LICENSE` is retained.
- OpenAI Documents is an external dependency and is not copied into this repository.
- No license was present for the original `narrative-review-workflow`. The repository owner must choose and add a license for that original material before granting downstream redistribution or modification rights. This does not affect local technical validation of the package.

See `THIRD_PARTY_NOTICES.md` for source and redistribution details.

`FILE_MANIFEST.sha256` records the SHA-256 of every packaged file other than the manifest itself. Paths are repository-relative.

## Known limits

- The explicit-only rule combines supported metadata (`allow_implicit_invocation: false`) with written skill instructions. It is not an operating-system or filesystem access control.
- Plugin identity is namespaced as `plugin-name:skill-name`; direct standalone skill names are not the invocation names documented for this package.
- GitHub import, plugin installation, fresh-session discovery, and invocation behavior require testing on the target Codex account/workspace after publication.
