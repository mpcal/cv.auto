# Bundled Typst packages

These packages are loaded through an explicit `package_path`. Rendering uses a
fresh temporary package cache and `ignore_system_fonts=True`; no global package
cache, system fonts or runtime download is required. Only runtime files and
licenses are included; examples and starter templates are omitted.

| Package | Version | Source | License |
| --- | --- | --- | --- |
| altacv | 1.7.0 | https://github.com/smur89/alta-typst | MIT |
| fontawesome | 0.6.2 | https://github.com/duskmoon314/typst-fontawesome | MIT |
| gairm-import | 0.8.1 | https://github.com/smur89/gairm-import | MIT |
| zebra | 0.1.0 | https://github.com/rojul/typst-zebra | MIT |

The files were copied from the package versions used by `typst-work/main.typ`.
Each package retains its own LICENSE and typst.toml.

## AltaCV adaptations for altacv-mod-1

- Allow annotated content for visible summaries without placing content objects
  in PDF metadata. The wrapper supplies plain-text descriptions and keywords.
- Render the annotation legend separately from the summary paragraph.
- Fall back to a website icon for unrecognized profile networks and use the URL
  when a profile has no username; username-only profiles remain unlinked.
- Preserve education summaries, highlights and named links, and avoid implying
  that an undated education end is an ongoing degree.
- Keep normal entries together while allowing entries exceeding one page to
  break. The wrapper supplies the A4 content dimensions through `_page_size_state`.
- Show certificate issuers even when upstream singleton grouping would hide them,
  and allow large issuer groups to flow across pages.
- Preserve textual language fluency instead of inferring dot ratings.

The wrapper retains the original package layout, uses `columnRatio: 1`, and
applies the `-0.15em` Font Awesome adjustment from `typst-work/main.typ`.
