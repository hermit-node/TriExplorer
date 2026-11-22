# TriExplorer code map

Find a feature by responsibility. Do not put feature logic back into main.py.

## Boot and UI

| File | Change here when you need to... |
| --- | --- |
| main.py | Change how the application starts (entry point only). |
| explorer.py | Change window creation, shared state, worker submission, shutdown, Qt startup. |
| controllers/layout.py | Change window layout, toolbar, pane containers, mode wiring. |
| controllers/sidebar.py | Change Quick Access pins, sidebar tree, folder expansion. |
| controllers/navigation.py | Change history, directory refresh, watcher, folder-size UI. |
| controllers/selection.py | Change selection, context menus and open-file behavior. |
| controllers/file_actions.py | Change clipboard, sequential rename, file operations UI. |
| controllers/archive_actions.py | Change extraction queue, ZIP password prompts and reuse. |
| controllers/duplicate_actions.py | Change SHA and ZIP/folder matching, cleanup workflows. |
| controllers/preview.py | Change image-preview lifecycle, cancel, animation. |
| panels.py | Change table columns, sorting, filters, row rendering, inline editor. |
| stylesheet.py | Change the Qt palette and the application QSS stylesheet. |
| app_icons.py and assets/ | Change taskbar icon or Windows AppUserModelID. |

Controller modules currently use feature mixins. They share the Explorer instance,
rather than independently scoped controller dependencies. This is an intentional
behavior-preserving first step, not yet fully decoupled composition. Each
controller explicitly imports its dependencies; avoid circular imports.

## Filesystem engines and configuration

| File | Responsibility |
| --- | --- |
| settings.py | Authority for settings.ini, defaults, cache path, network-trash name. |
| settings.example.ini | Documented editable INI defaults. |
| filters.py | File-extension categories and pane presets. |
| file_types.py | Qt-independent Entry, normalized, readable_size definitions. |
| services/filesystem.py | Enumeration, safe transfers and path validation. |
| services/recycle.py | Network-share trash, Windows recycling and restore. |
| services/archive.py | ZIP creation, validation, extraction and batch helpers. |
| duplicates.py | Size, sample and SHA matching plus SHA cache. |
| archive_matches.py | ZIP versus extracted folder SHA verification. |
| folder_sizes.py | SQLite folder-size cache. |
| workers.py | Generic Qt background job wrapper. |
| appcore.py | Compatibility imports ONLY; put no new behavior here. |

## Flow and invariants

- main.py -> explorer.py -> controllers/ -> services/.
- file_types.py and filters.py are independent of Qt.
- settings.ini is user-local state and is gitignored.
- assets/triexplorer.svg is editable art. assets/triexplorer.ico contains
  PNG-backed icon resources in sizes from 16 through 256 pixels.
- SHA duplicate and ZIP/folder cleanup require verification before recycling.
- Network trash remains on its source share; there is no direct-delete fallback.
- Collapsed image previews do not decode new images.

## Validate before committing

Run in the project directory on KHzz:

    $env:QT_QPA_PLATFORM="offscreen"
    $env:QT_QPA_FONTDIR="C:\Windows\Fonts"
    py -3.14 -B test_explorer.py

Check startup from the KH24 mapped checkout and keep both icon assets together.
