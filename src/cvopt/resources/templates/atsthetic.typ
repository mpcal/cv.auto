// Adapted from atsthetic-cv 0.1.0 (MIT); see atsthetic-LICENSE.
// Local adaptation: no icon or skill registry dependencies; localized header.

/// Generate the building blocks of your cv
/// - name (str):
/// - title (content): Your current job title
/// - additional-info (content, none): Optional information to show under your
///   title
/// - summary (content): A summary to show under your socials
/// - email (str): Your email address, this is added to the document metadata
/// - socials (dictionary): Maps a social platform label (e.g. `Website`, `Github`)
///   to a tuple of `(url, display-content)`, where `url` is the link target
///   (e.g. `mailto:`, `tel:`, or `https://`) and `display-content` is the
///   rendered label
/// - colors (dictionary):Theme colors, with keys `foreground`, `muted`, and
///   `accent`, each a `color`
/// - font-size (length):
/// - font-family (str):
#let generate-blocks(
  name: "Jane Doe",
  title: [Full-stack Developer],
  additional-info: [Based in Madrid, Spain (UTC+1)],
  summary: [Full-stack developer with 3+ years of experience building production
    systems end to end, from frontend interfaces to backend infrastructure and
    DevOps tooling.],
  email: "jane.doe@example.com",
  socials: (
    Website: ("https://example.com", [example.com]),
    Tel: ("tel:+15551234567", [+1 (555) 123-4567]),
    Github: ("https://github.com/janedoe", [\@janedoe]),
  ),
  colors: (
    foreground: rgb("#504945"),
    muted: rgb("#504945").transparentize(85%),
    accent: rgb("#B57614"),
  ),
  summary-label: "Summary",
  contacts: (),
  font-size: 10pt,
  font-family: "Space Grotesk",
) = {
  let init-cv(body) = {
    /**
     * Set rules
     */
    set document(
      title: "Curriculum Vitae / Resume",
      author: name + " <" + email + ">",
      keywords: ("cv", "resume"),
      date: datetime.today(),
    )

    set text(font: font-family, fill: colors.foreground, size: font-size)

    set page(margin: 24pt)

    /**
     * Show rules
     */
    show heading.where(level: 4): set text(
      fill: colors.foreground.transparentize(30%),
    )

    body
  }

  /// A section is just a stylized heading
  let section(level: 2, radius: 2pt, body) = block(
    inset: (y: 0.5em),
    outset: (x: 1em),
    above: 1.5em,
    fill: colors.muted,
    width: 100%,
    radius: radius,
    heading(level: level, text(size: font-size, body)),
  )

  /// An activity can be a job position, education, certificate accomplishment,
  /// etc
  /// - right (content, none): What to display on the right side of the
  ///   activity. Can be period of time, level of proficiency, etc.
  /// - level (int): The heading level of an activity to be outlined
  /// - separator (boolean): Show or hide the left and right separator
  /// - separator-params (dictionary): `line` parameters for the separator
  /// - gutter (length): Gap in between the left, separator and right
  let activity(
    right: none,
    level: 3,
    href: none,
    separator: true,
    separator-params: (
      length: 100%,
      stroke: (
        dash: "loosely-dotted",
      ),
    ),
    gutter: 1em,
    body,
  ) = heading(level: level, grid(
    align: horizon,
    gutter: gutter,
    columns: (1fr, 1em, auto),
    if type(href) == type("") { link(href, body) } else { body },
    if separator {
      line(..separator-params)
    } else { none },
    if right != none {
      text(fill: colors.foreground.transparentize(30%), weight: "thin", [#text(
          fill: white.transparentize(100%),
          [],
        ) #right])
    } else { none },
  ))

  let socials-block(separator: h(1em)) = (
    [Email: #link("mailto:" + email, email)],
    ..socials.pairs().map(((k, (l, v))) => [#k: #link(l, v)]),
  ).join(separator)

  let header(profile-image: none, radius: 3pt) = {
    let with-image = profile-image != none

    let content = [
      // name
      = #block(inset: (bottom: 0.2em), text(size: 1.5em, name))

      // job title
      #block(text(size: 1.5em, title))
      #if additional-info != none { additional-info }
      #line(length: 100%, stroke: colors.muted)
      #contacts.join(" · ")

      #if summary != [] {
        heading(level: 2, text(size: font-size, summary-label))
        summary
      }
    ]

    block(
      fill: colors.muted,
      outset: (x: 1em),
      inset: (y: 1.3em),
      width: 100%,
      radius: radius,
      grid(
        columns: (auto, 1fr),
        gutter: 1em,
        ..if with-image {
          (
            profile-image,
            content,
          )
        } else { (content,) },
      ),
    )
  }

  let profile-block = block.with(radius: 3pt, clip: true)

  (
    header: header,
    section: section,
    activity: activity,
    profile-block: profile-block,
    init-cv: init-cv,
  )
}
