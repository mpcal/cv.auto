// Lays out the render model in model.json. Strings are inserted as values, never
// evaluated as markup, so CV text needs no escaping.
#let m = json(sys.inputs.model)
#let L = m.labels
#set document(title: m.name)
#set page(paper: "a4", margin: m.margin_mm * 1mm)
#set text(font: m.font, size: m.font_size_pt * 1pt, lang: m.lang, hyphenate: false)
#set par(justify: false, leading: 0.55em)
#set list(indent: 0.6em, body-indent: 0.5em, spacing: 0.45em)
#show heading: it => block(above: 1.1em, below: 0.6em, text(size: 1.1em, weight: "bold", upper(it.body)))

#let stmt(s) = {
  let body = if s.mark == "I" { highlight(fill: rgb("#fde2e2"), s.text) }
    else if s.mark == "E" { highlight(fill: rgb("#fff1c2"), s.text) }
    else { s.text }
  if s.row > 0 { [#body#super(s.mark + str(s.row))] } else { body }
}

#let line-entry(left, dates) = block(below: 0.4em)[#left #h(1fr) #dates]

// Header
#text(size: 16pt, weight: "bold", m.name)
#if m.headline != none [ \ #text(size: 11pt, stmt(m.headline)) ]
#if m.contact.len() > 0 [ \ #m.contact.join(" · ") ]

#if m.mode == "annotated" and m.legend != "" {
  block(above: 0.8em, inset: 6pt, stroke: 0.5pt + gray, text(size: 0.85em, m.legend))
}

#if m.summary.len() > 0 [
  #heading(level: 1, L.summary)
  #m.summary.map(stmt).join(" ")
]

#if m.work.len() > 0 [
  #heading(level: 1, L.experience)
  #for p in m.work [
    #line-entry([*#p.title*, #p.employer#if p.location != "" [, #p.location]], p.dates)
    #for s in p.highlights [- #stmt(s)]
  ]
]

#if m.education.len() > 0 [
  #heading(level: 1, L.education)
  #for e in m.education [
    #line-entry([*#e.title*#if e.detail != "" [, #e.detail]], e.dates)
  ]
]

#if m.skills.len() > 0 [
  #heading(level: 1, L.skills)
  #for g in m.skills [
    #block(below: 0.4em)[#if g.name != "" [*#g.name:* ]#g.keywords.map(stmt).join(", ")]
  ]
]

#if m.languages.len() > 0 [
  #heading(level: 1, L.languages)
  #m.languages.join(", ")
]

#if m.certificates.len() > 0 [
  #heading(level: 1, L.certificates)
  #for c in m.certificates [
    #line-entry([*#c.title*#if c.detail != "" [, #c.detail]], c.dates)
  ]
]

// Appendix: the report (annotated only)
#if m.mode == "annotated" {
  pagebreak()
  heading(level: 1, L.report)
  for line in m.report_summary [#line \ ]
  v(0.6em)
  set text(size: 0.8em)
  let fmt(x) = if x == none { "" } else { x }
  table(
    columns: (auto, 1fr, auto, auto, auto, auto),
    stroke: 0.4pt + gray,
    inset: 4pt,
    table.header(..(L.col_row, L.col_claim, L.col_fidelity, L.col_provability, L.col_impact,
      L.col_requirement).map(h => strong(h))),
    ..m.report.map(r => (str(r.row), r.claim, fmt(r.fidelity), fmt(r.provability),
      str(calc.round(r.impact, digits: 1)), r.requirements.join(", "))).flatten()
  )
}
