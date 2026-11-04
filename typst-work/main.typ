#import "@preview/altacv:1.7.0": alta-from-json

// Align the visible centres of FontAwesome icons with the adjacent text.
#show text: it => context {
  let fonts = if type(text.font) == str { (text.font,) } else { text.font }
  if fonts.any(font => lower(font).starts-with("font awesome")) {
    move(dy: -0.15em, it)
  } else { it }
}

#alta-from-json(path("resume.json"), preferences: (columnRatio: 1))
