// AltaCV 1.7.0 with the typst-work icon adjustment and cvopt annotations.
// All CV strings are values, never evaluated as Typst markup.
#import "@preview/altacv:1.7.0": alta, _page_size_state
#let m = json(sys.inputs.model)
#let L = m.labels
#let margin = {
  if m.margin_mm == none { (x: 9mm, y: 15mm) }
  else { (x: m.margin_mm * 1mm, y: m.margin_mm * 1mm) }
}
#set page(paper: "a4", margin: margin)
#set text(font: m.font, size: m.font_size_pt * 1pt, lang: m.lang)
#set document(
  description: m.basics.summary,
  keywords: m.skills.map(g => g.keywords.map(s => s.text)).flatten().dedup(),
)
#_page_size_state.update((width: 210mm - 2 * margin.x, height: 297mm - 2 * margin.y))

// Keep exactly the icon alignment used by typst-work/main.typ.
#show text: it => context {
  let fonts = if type(text.font) == str { (text.font,) } else { text.font }
  if fonts.any(font => lower(font).starts-with("font awesome")) {
    move(dy: -0.15em, it)
  } else { it }
}

#let stmt(s) = {
  let body = if s.mark == "I" { highlight(fill: rgb("#fde2e2"), s.text) }
    else if s.mark == "E" { highlight(fill: rgb("#fff1c2"), s.text) }
    else { s.text }
  if s.row > 0 { [#body#super(s.mark + str(s.row))] } else { body }
}
#let cv = (
  basics: (
    ..m.basics,
    label: if m.headline != none { stmt(m.headline) } else { none },
    profiles: m.basics.profiles.filter(p => p.url != "" or p.username != ""),
    summary: m.summary.map(stmt).join(" "),
    annotationLegend: if m.mode == "annotated" and m.legend != "" {
      block(below: 0.8em, inset: 6pt, stroke: 0.5pt + gray,
        text(size: 0.85em, m.legend))
    },
  ),
  work: m.work.map(p => (
    position: p.title,
    name: p.employer,
    location: if p.location != "" { p.location } else { none },
    startDate: p.start_date,
    endDate: p.end_date,
    highlights: p.highlights.map(stmt),
  )),
  education: m.education.map(e => (
    studyType: e.title,
    institution: e.detail,
    startDate: e.start_date,
    endDate: e.end_date,
    summary: e.summary,
    highlights: e.highlights,
    courses: e.courses,
    score: e.score,
    links: e.links,
  )),
  skills: m.skills.map(g => (name: g.name, keywords: g.keywords.map(stmt))),
  languages: m.languages,
  certificates: m.certificates,
  // One line per award: title · awarder · date, then the summary.
  awards: m.awards.map(a => {
    let parts = a.date.split("-")
    let date = if parts.len() == 2 { m.months.at(int(parts.at(1)) - 1) + " " + parts.at(0) }
      else { a.date }
    (title: (a.title, a.awarder, date).filter(x => x != "").join(" · "), summary: a.summary)
  }),
)
#alta(cv, labels: (
  lang: m.lang,
  work: L.experience,
  skills: L.skills,
  languages: L.languages,
  education: L.education,
  certificates: L.certificates,
  awards: L.awards,
  present: L.present,
  months: m.months,
), preferences: (
  columnRatio: 1,
  font: m.font,
  bodySize: m.font_size_pt * 1pt,
  margin: margin,
))

// Appendix lives outside the CV's tag and section layout.
#if m.mode == "annotated" {
  pagebreak()
  heading(level: 1, upper(L.report))
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
