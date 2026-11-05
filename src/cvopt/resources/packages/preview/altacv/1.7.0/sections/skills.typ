// `_skills` and `_interests` consume the same JSON Resume
// `{name, keywords}` shape and render identically (a row of pill
// tags, led by the group name as plain text). The shared layout lives in
// `_name_keywords_section`; the two public renderers differ only in
// which `labels.*` heading they pass. Neither reads `prefs`; it's
// accepted so every renderer shares the `(entries, labels, prefs)`
// dispatch signature.

#import "../internal/state.typ": _accent_state, _body_size_state
#import "../internal/primitives.typ": _tag_row

// cvopt: group names are plain bold accent text in a column of their own,
// so only the keywords are pills.
#let _name_keywords_section(groups, heading) = {
  // Filter empty-keywords groups up front so we can also skip the
  // section heading: rendering a bare "Skills" with nothing underneath
  // (every group had an empty `keywords:`) reads worse than skipping
  // the section entirely.
  let visible = groups.filter(g => g.at("keywords", default: ()).len() > 0)
  if visible.len() == 0 { return }
  context {
    let body-size = _body_size_state.get()
    let row-gap = 0.7 * body-size
    let named = visible.any(g => g.at("name", default: "") != "")
    [== #heading]
    grid(
      columns: if named { (auto, 1fr) } else { (1fr,) },
      column-gutter: 0.8 * body-size,
      row-gutter: row-gap,
      ..visible
        .map(group => {
          let row = (par(leading: row-gap, _tag_row(group.keywords)),)
          if named {
            row.insert(0, text(weight: "bold", fill: _accent_state.get(), group.at(
              "name",
              default: "",
            )))
          }
          row
        })
        .flatten(),
    )
  }
}

#let _skills(groups, labels, prefs) = _name_keywords_section(
  groups,
  labels.skills,
)

#let _interests(groups, labels, prefs) = _name_keywords_section(
  groups,
  labels.interests,
)
