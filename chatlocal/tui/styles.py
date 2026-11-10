"""Textual CSS: dark base, restrained cyan (primary) and purple (secondary) accents."""

CSS = """
Screen { background: #0f1117; color: #d6dae3; }

#header { height: 1; padding: 0 1; background: #161a23; color: #d6dae3; }
#body { height: 1fr; }
#status { height: 1; padding: 0 1; background: #161a23; color: #a78bfa; }
Footer { background: #161a23; }
Footer > .footer--key { color: #22d3ee; background: #1f2633; }

#sidebar { width: 18; border: none; border-right: tall #232a38; background: #12151d; padding: 1 0; }
#sidebar:focus { border-right: tall #22d3ee; }
#sidebar > .option-list--option-highlighted { background: #1f2633; color: #22d3ee; text-style: bold; }
#sidebar:focus > .option-list--option-highlighted { background: #155e75; color: #ffffff; }
.-narrow #sidebar { width: 14; }

#content { width: 1fr; height: 1fr; }
#offline { width: 1fr; height: 1fr; padding: 2 4; content-align: center middle; }

View { height: 1fr; padding: 0 1; }
.view-title { height: 1; margin: 1 0 0 0; color: #22d3ee; }
.state { height: auto; padding: 0 1; display: none; }
.state.-loading { color: #eab308; }
.state.-error { color: #ef4444; }
.state.-empty { color: #7f8a9a; }
.state.-info { color: #a78bfa; }
.hint { color: #7f8a9a; height: auto; }
.warn { color: #eab308; height: auto; }

DataTable { height: 1fr; background: #0f1117; border: round #232a38; }
DataTable:focus { border: round #22d3ee; }
DataTable > .datatable--header { background: #161a23; color: #a78bfa; text-style: bold; }
DataTable > .datatable--cursor { background: #1f2633; color: #ffffff; }
DataTable:focus > .datatable--cursor { background: #155e75; color: #ffffff; text-style: bold; }
DataTable > .datatable--even-row { background: #12151d; }

.cards { height: auto; }
.card { width: 1fr; height: auto; min-height: 6; border: round #232a38; padding: 0 1; margin: 0 1 0 0; }
.card-title { color: #a78bfa; text-style: bold; }
.-narrow .card { min-height: 5; }

.detail { height: auto; max-height: 12; border: round #232a38; padding: 0 1; overflow-y: auto; }
.-narrow .detail { max-height: 8; }

/* Scrolling body of a full page, so its button row stays on screen when rows are short. */
.page-flex { height: 1fr; min-height: 3; width: 1fr; }
.page-flex Input { width: 1fr; }

.row { height: auto; margin: 0 0 1 0; }
.label { width: 16; padding: 1 1 0 0; color: #a7b0c0; }
.row Input { width: 1fr; }
Input { border: tall #232a38; background: #12151d; }
Input:focus { border: tall #22d3ee; }
Button { min-width: 10; margin: 0 1 0 0; }
Button.-primary { background: #0e7490; color: #ffffff; }
Button.-error { background: #7f1d1d; }
.buttons { height: auto; margin: 1 0 0 0; }
Checkbox { background: transparent; border: none; padding: 0; height: 1; }
Checkbox:focus > .toggle--label { color: #22d3ee; text-style: bold; }
Checkbox.-blocked { color: #4b5563; }
.search-bar { height: auto; }
.search-bar Input { width: 1fr; }

ModalScreen { align: center middle; background: #0f1117 70%; }
/* Dialog height is set by chatlocal.tui.modals.Dialog; the body takes the rest so the
   error line and buttons are always inside the border. */
.dialog { width: 84; max-width: 96%; height: auto; max-height: 100%; border: thick #22d3ee;
          background: #161a23; padding: 1 2; }
.dialog-flex { height: 1fr; min-height: 3; width: 1fr; }
.dialog-flex Input, .dialog-flex Select { width: 1fr; }
/* Long permission labels wrap inside the scroll body instead of running under its scrollbar. */
.dialog-flex Checkbox { width: 1fr; height: auto; }
.dialog.-danger { border: thick #ef4444; }
.dialog.-secret { border: thick #a78bfa; }
.dialog-title { text-style: bold; color: #22d3ee; margin: 0 0 1 0; }
.-danger .dialog-title { color: #ef4444; }
.-secret .dialog-title { color: #a78bfa; }
.dialog-body { height: auto; }
.columns { height: auto; }
.col { width: 1fr; height: auto; }
#group-list, #friend-list { height: auto; max-height: 8; }
.-short #group-list, .-short #friend-list { max-height: 5; }
#text { height: 1fr; min-height: 3; }
.secret-box { background: #0f1117; color: #e9d5ff; padding: 0 1; border: round #a78bfa; height: auto; }
.error-text { color: #ef4444; height: auto; }

/* ---- full-port pages ---------------------------------------------------- */
#sidebar { overflow-y: auto; scrollbar-size-vertical: 1; }
/* List on the left, detail on the right; both take the page's spare rows. */
.split { height: 1fr; min-height: 6; }
.split > DataTable { width: 2fr; height: 1fr; }
.split > .pane { width: 3fr; height: 1fr; border: round #232a38; padding: 0 1; }
.split > .pane:focus-within { border: round #22d3ee; }
.split > #coll-items, .split > #chat-msg-table { width: 3fr; }
.-narrow .split > DataTable { width: 1fr; }
.-narrow .split > .pane, .-narrow .split > #coll-items, .-narrow .split > #chat-msg-table { width: 1fr; }
.pane Static { height: auto; }
.-short .detail { max-height: 5; }
.-short .card { min-height: 4; }
.-short View > .hint { max-height: 2; }
View > .hint { height: auto; max-height: 3; overflow: hidden; }
/* Shared form dialog fields. */
.form-area { width: 1fr; min-height: 3; border: tall #232a38; }
.form-area:focus { border: tall #22d3ee; }
.form-multi { height: auto; max-height: 8; width: 1fr; }
.-short .form-multi { max-height: 5; }
.dialog-flex TextArea { width: 1fr; }
#choice-list { height: auto; max-height: 16; }
#reply-text { width: 1fr; }
.buttons { height: auto; }
.buttons Button { margin: 0 1 0 0; }
.-narrow .buttons Button { min-width: 6; }
/* Dialog actions wrap onto a grid so six buttons still fit an 80-column screen. */
.dialog .buttons { layout: grid; grid-size: 3; grid-rows: 3; grid-gutter: 0 1; height: auto; }
.dialog .buttons Button { width: 1fr; min-width: 6; margin: 0; }

/* SL Docker panel: fixed above the scrolling body so its buttons never scroll away. */
#mg-docker { height: auto; border: round #155e75; padding: 0 1; margin: 0 0 1 0; }
#mg-docker:focus-within { border: round #22d3ee; }
#mg-docker-title, #mg-docker-state { height: auto; }
#mg-docker-buttons { layout: grid; grid-size: 3; grid-columns: 2fr 1fr 1fr; grid-gutter: 0 1; height: auto; }
#mg-docker-buttons Button { width: 1fr; min-width: 8; margin: 0; }
#mg-docker-note { max-height: 2; overflow: hidden; }
.-short #mg-docker { margin: 0; }
.-short #mg-docker-note { max-height: 1; }
#sl-docker-bar { height: auto; margin: 0 0 1 0; }
#sl-docker { min-width: 18; margin: 0 1 0 0; }
#sl-docker-bar .hint { width: 1fr; padding: 1 0 0 0; }
.-short #sl-docker-bar { margin: 0; }
"""
