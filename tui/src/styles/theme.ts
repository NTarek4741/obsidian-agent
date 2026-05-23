// Obsidian-flavored dark palette. Lavender purple accent, deeper purple
// selection highlight, dark gray surfaces with a visibly lifted sidebar.
export const palette = {
  bg:            "#181818",
  bgLifted:      "#232323",
  boxBorder:     "#3a3a3a",
  accent:        "#a78bfa",
  accentDeep:    "#7c3aed",
  text:          "#dcddde",
  dim:           "#9e9e9e",
  dimmer:        "#5e5e5e",
  thinkingAmber: "#fab283",
  statusGreen:   "#3fb950",
  statusRed:     "#f85149",
  warning:       "#f5a742",
  mdCodeBg:      "#1f1f1f",
} as const;

// Semantic tokens — components MUST read from these, not from hex literals.
export const tokens = {
  // Surfaces
  bg: palette.bg,
  sidebarBg: palette.bgLifted,

  // Box / border / accent
  boxBorder: palette.boxBorder,
  accent: palette.accent,
  accentDeep: palette.accentDeep,

  // Text hierarchy
  textPrimary: palette.text,
  textDim: palette.dim,
  textDimmer: palette.dimmer,
  textInverse: palette.text,

  // Cursor / selection — strong purple for selection highlights
  cursorBg: palette.accentDeep,

  // Status / state
  toolRunning: palette.accent,
  toolDone:    palette.statusGreen,
  toolFailed:  palette.statusRed,
  toolBg:      palette.bg,
  toolHeader:  palette.accent,
  statusGreen: palette.statusGreen,
  statusRed:   palette.statusRed,

  // Accents
  thinkingAmber: palette.thinkingAmber,
  warning: palette.warning,

  // Markdown
  mdHeading:    palette.text,
  mdStrong:     palette.text,
  mdEm:         palette.text,
  mdCode:       palette.text,
  mdCodeBg:     palette.mdCodeBg,
  mdLink:       palette.accent,
  mdBlockquote: palette.dim,
  mdHr:         palette.boxBorder,
  mdListBullet: palette.accent,
} as const;
