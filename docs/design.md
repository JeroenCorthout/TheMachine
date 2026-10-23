# Design

One design system for the dashboard, the hosted app and the website. Its tokens sit between `design-tokens:start` and
`design-tokens:end` at the top of `dashboard.html`; the hosted service carries the identical block, and a test there
keeps the two equal. Change a token in both places at once.

The model is the calm, editorial feel of Claude's apps and sites, in our own colours, with our mascot and logo. What
makes that feel is mostly restraint: warm neutral paper, ink text, one accent used rarely, hairlines instead of shadows,
and a serif that only speaks at the top of the hierarchy.

## What we take from Claude's design

Observed on claude.ai, claude.com and anthropic.com (public pages, light and dark, 390px and 1280px):

- **Paper and ink.** A warm off-white page (`#FAF9F5`), pure white cards on it, and a slightly darker "sunken" tone
  for tracks and quiet fills. Text is near-black ink, with two greys for secondary and tertiary text. Dark mode is the
  same idea inverted: a warm near-black page, cards one step lighter, ivory text. No gradients, no tinted borders.
- **The accent is rare.** Claude's clay orange appears in the logo and on one or two actions; everything else is
  neutral. The main call to action is often plain ink (near-black in light, ivory in dark).
- **Serif for voice, sans for interface.** Display and page headings are a serif at a light-to-regular weight (330 to
  500), normal letter-spacing, generous size. Everything you operate (buttons, tabs, labels, card titles, menus) is a
  sans at 400 or 500. No bold serif, no italics for emphasis, no uppercase tracking for labels except the tiniest.
- **A small, tight type scale.** 12, 13.5, 15 (body), 17, 20, 26, and a large display size. Body line-height about 1.5;
  headings 1.1 to 1.25.
- **Hairlines, not boxes.** Cards are a 1px line at about 10% ink and a large radius (12 to 16px), with at most a
  barely visible shadow in light mode and none in dark. Lists inside a card are rows divided by a line.
- **Buttons.** 8px radius, 500 weight, about 36 to 40px tall. Primary is filled; secondary is a white (dark: lifted)
  fill with a hairline; tertiary is text only with a soft fill on hover.
- **Segmented controls.** A sunken track with the current option raised on it. Chips and filter pills are fully round,
  sunken fill, small sans text.
- **Inputs.** White (dark: sunken) fill, hairline border, 8 to 10px radius; focus darkens the border and adds a soft ring.
- **Menus and dialogs.** Raised surface, hairline, 12 to 16px radius, one soft shadow. The only place shadows are strong.
- **Hover and focus.** Hover changes the fill a little, never the border colour or size; focus is a visible ring.
- **Motion.** Short (about 150ms) and eased out; nothing bounces.
- **Density and alignment.** Content sits in one reading column, left aligned, with consistent 8px-based gaps; air
  comes from the spacing between groups, not from big paddings inside them.
- **Icons.** Thin, rounded line icons (about 1.5 to 1.75px stroke) at 16 to 20px, in the text colour.

## Our translation

| Role | Light | Dark | Token |
| --- | --- | --- | --- |
| Page | warm paper `#F8F7F3` | warm charcoal `#1B1C1F` | `--c-bg` |
| Card | white | `#232428` | `--c-surface` |
| Lifted (secondary buttons, popovers in dark) | `#FFFFFF` | `#2C2D32` | `--c-raised` |
| Sunken (tracks, chips, code, quiet fills) | `#EFEDE6` | `#151619` | `--c-sunken` |
| Line / strong line | ink at 10% / 18% | white at 9% / 17% | `--c-line`, `--c-line-strong` |
| Text / secondary / tertiary | `#1D1D1B` / `#5A5954` / `#75736D` | `#F3F0E8` / `#C4C1B8` / `#94918A` | `--c-text`, `--c-text-2`, `--c-text-3` |
| Accent (mint) | `#4E6B34` | `#BCCBA8` | `--c-accent` |
| Accent tint (current tab, selected) | mint at 15% on white | a greener mint at 17% on the card | `--c-accent-tint` |
| Attention (amber), warning (orange), Network (lavender) | `#8A5C00`, `#B0561A`, `#5E4F86` | `#FFD76B`, `#ECA45B`, `#C7B5E2` | `--c-amber`, `--c-orange`, `--c-lavender` |

- **Mint is our clay.** One filled mint button per card or page (the thing to do), the live dot, the current tab's
  tint, focus rings. Not headings, not links, not hero text.
- **Cream warms the dark text** (`#F3F0E8`) instead of colouring headings. Headings are ink in both themes.
- **Typefaces.** Source Serif 4 (with its optical sizes, so display sizes get the fine display cut) for the display
  line, page titles, section heads and dialog titles, at weight 380 to 460. Inter for everything else at 400 and 500,
  600 only for card titles and numbers. Both are bundled under the SIL Open Font License.
- **The mascot and logo** stay as they are: the mascot beside the title in the dashboard, on empty states and in the
  website's hero; the wordmark in the website's header.

## Components

- **Layout**: every tab, and Settings, uses the same reading column: `--measure` (720px) plus 24px gutters, so `main`
  is 768px. No tab sets its own width, and the page keeps the scrollbar's room (`scrollbar-gutter: stable`), so a
  short tab like Events doesn't shift or look narrower than a long one.
- **Phone header bars** (the top bar, a card's chat): the buttons at either end are 44px boxes whose glyphs sit on the
  content's gutters (☰ and + on the cards' edges, ‹ and ⋯ on the chat's text and bubbles), every glyph on the title's
  cap-height centre line, and a long title ends in an ellipsis before the right button.
- **Sidebar**: one component for every width. From 1024px it is a column on the left: the tabs as quiet rows with
  icons (Suggestions, Calendar, Events, Graph, Feedback, Connections), Find a card, the cards by section (Done folded), and Browser, Settings and the status at the bottom; it folds
  to icons (Ctrl/⌘+B). Graph has its own rows, People, Organisations and Projects, each a coloured square and a count,
  under a fold that is remembered; a row opens that pane and the address keeps it (`#graph=orgs`). Below 1024px the
  same sidebar is a drawer under a slim top bar with a menu button: a swipe from the left edge or All tasks in a chat
  also opens it, and ✕, the shade, Esc, back or a swipe to the left closes it, as does picking a row. On a phone, Find
  a card is a search icon in the top bar of Suggestions, left of the +: a tap turns the bar into the field with Cancel
  and the matching cards under it. Each page has a serif title in the column, except Suggestions: there the tagline is
  a quiet serif line at body size, muted, with the counts under it, so the cards lead; a phone starts at the first
  section head, which carries the counts, and keeps only the all-clear line.
- **Boards** (Graph › Projects): Board is the default, List / Board in the header, remembered per device. Columns are
  the stages, with "Stalled or declined" as a row underneath; chips switch boards. A tile: logo, name, next step, why
  it last moved with the date (opens the message), and a no-reply chip from 10 days. Eight tiles a column, then
  "Show N more". Drag a tile, or use its ⋯ menu. Under 900px the columns scroll sideways and snap; on a phone one
  stage at a time with stage tabs, and the board is picked from a menu.
  A suggested board is one calm box above it ("Start a board for Hiring?", its stages as a flow, Save, Edit, Not now),
  not a card. The editor is a dialog, a bottom sheet on a phone, with Save kept in reach at its foot; a removed stage
  says where its projects go, and Delete asks once more.
- **Calendar**: the next seven days, read-only. An agenda by day (Overdue, Today, Tomorrow, then each weekday), one
  row per item: the time (or Due, All day), a tone dot (a ring for a meeting, amber due, orange overdue, lavender coming
  back or a check-in, mint a promise), the title and one quiet line. A meeting's card shows as a chip (in the line on a
  phone). On a computer a Week toggle shows seven columns instead; the choice is kept on the device.
- **Sub-tabs** (Fleet, Graph, Connections): a row of round pills under the tabs, the current one tinted, with a count
  (orange when something needs you). It scrolls and fades like the tabs on a phone. Each pane is drawn at once and
  shown one at a time; the choice is kept, and links open one (`#graph=roles`). On the Graph the address follows the pane.
- **Section heads**: the serif heading and its count as a small pill on one line, a one-line explainer below.
- **Cards**: surface, 1px line, 14px radius; a slim tone mark at the top left for urgency; title in sans 600;
  body in secondary text; actions left to right, one primary.
- **Buttons**: primary (mint fill), secondary (lifted fill and hairline), quiet (text; on hover a rounded soft tint with room around the text, its negative margin keeping the layout still). Links styled as buttons match the others: no underline, same height, centred.
  8px radius, 500 weight.
- **Chips**: round, sunken fill, 12px sans 500, with a 6px dot in the tone colour.
- **Inputs**: hairline, 8px radius, mint border and soft ring on focus.
- **Empty states**: the mascot, one short serif line, one muted sentence; no dashed boxes.
- **Eyebrows** (small labels above headings): 12.5px sans 500 in tertiary text, sentence case.

## Checking it

Run the sandbox (`scripts/sandbox.sh shots`) and the hosted service's sandbox, and look at 390px and 1280px in light and
dark. The shots runner fails on anything wider than the screen, overlapping controls and wrapped dates.
