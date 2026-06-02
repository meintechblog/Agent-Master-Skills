# ChatGPT.app — AppleScript UI-Tree-Pfade

Stand 2026-05-27, ChatGPT-Desktop-App. Pfade sind **typed references** — kein `UI element N` benutzen (das zählt typed-counter und ist unreliable).

## Top-Level

```
window 1
├── group 1                         (outer AXGroup, fills window)
│   └── splitter group 1
│       ├── group 1                 (sidebar)
│       │   ├── group 1             (search bar wrapper)
│       │   │   └── text field 1    ← Sidebar-Such-Field
│       │   ├── scroll area 1       (chat list)
│       │   │   └── list 1
│       │   │       └── list N      (sublist nach Treffer-Gruppe)
│       │   │           └── group X
│       │   │               └── group 1
│       │   │                   └── button 1    ← Chat-Eintrag (clickable)
│       │   └── menu button 1       (User-Menu am unteren Sidebar-Rand)
│       ├── splitter 1              (zwischen Sidebar und Main)
│       └── group 2                 (main pane)
│           ├── scroll area 1       (Chat-Verlauf)
│           │   └── list 1
│           │       └── list 1
│           │           └── group N (jede Message-Bubble)
│           │               └── group 1
│           │                   └── button 1    ← AXButton mit dem Bild (size 866x437 bei AI-Image)
│           ├── button 1            (manchmal: "Reply-Suggestion" oben)
│           ├── scroll area 2       (zero-size)
│           ├── scroll area 3       ← INPUT-WRAPPER
│           │   └── UI element 1    (AXTextArea, akzeptiert Cmd+V)
│           ├── button 1            (toggle attach-menu, "+")
│           ├── button 2            (web-search, "🌐")
│           ├── button 3            (analyze, "🔍")
│           ├── button 4            (canvas, "A")
│           ├── button 5            (model-selector, e.g. "Auto")
│           ├── button N (variabel) (recording / mic)
│           └── button (letzter)    (Send / Stop, runder Button rechts unten)
├── toolbar 1                       (window title bar)
│   ├── button 1 (Sidebar-toggle)
│   ├── button 2 (Neuer Chat)       ← NEW-CHAT-BUTTON
│   ├── button 3 (Model-Selector "ChatGPT Auto")
│   ├── button 4 (Weitergeben)
│   └── button 5 (Zum neuen Fenster wechseln)
├── button 3 (Close-Button)
├── button 4 (Vollbild)
└── button 5 (Minimieren)
```

## Wichtigste Snippets

**Neuer Chat öffnen:**
```applescript
tell application "ChatGPT" to activate
delay 0.4
tell application "System Events"
  tell process "ChatGPT"
    set frontmost to true
    delay 0.2
    click button 2 of toolbar 1 of window 1
    delay 1.0
  end tell
end tell
```

**Input fokussieren:**
```applescript
tell application "System Events"
  tell process "ChatGPT"
    set inputArea to UI element 1 of scroll area 3 of group 2 of splitter group 1 of group 1 of window 1
    click inputArea
    delay 0.5
  end tell
end tell
```

**Latest AI-Image-Button finden (für Right-Click):**
```applescript
tell application "System Events"
  tell process "ChatGPT"
    set imgArea to scroll area 1 of group 2 of splitter group 1 of group 1 of window 1
    set outerList to list 1 of imgArea
    set innerList to list 1 of outerList
    set foundBtn to missing value
    repeat with bubble in (groups of innerList)
      try
        set btn to button 1 of group 1 of bubble
        if (item 2 of size of btn) > 400 then set foundBtn to btn
      end try
    end repeat
    -- foundBtn = das Bild der letzten AI-Antwort
  end tell
end tell
```

**Polling auf Done:**
- `static texts of imgArea contains "Bild wird erstellt"` → noch am generieren
- Sonst image-Button mit height > 400 da → done

## Gotchas

- `UI element N of <parent>` zählt nicht positional, sondern in einer typed-Reihenfolge (alle scroll areas zuerst, dann buttons, dann static texts, …). Immer typed-References nutzen (`button N`, `group N`, `scroll area N`).
- Beim Sidebar-Chat-Click: nach `click button 1 of group 1 of group X of ...` immer `delay 1.0` einplanen — Main-Pane lädt async.
- ChatGPT.app öffnet manchmal zusätzliche Modal-Windows (z.B. „Was gibt's Neues") — die sind `window 2`/`window 3`. Probe via `count of windows` vor Operationen.
- Bei `entire contents of window 1` gibt's manchmal Permission-Denied-Errors. Lieber manuelle Iteration mit BFS und try-blocks.

## Cliclick

`cliclick rc:X,Y` = right-click at absolute screen coords. Für Image-Mitte:
```bash
read CX CY <<<"$(osascript ... extract from foundBtn ...)"
cliclick rc:$CX,$CY
```

Right-Click-Menü hat als 1. Item typischerweise "Bild kopieren" (Down + Return).
