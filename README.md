# TriExplorer

A native PySide6 Windows file explorer. One selected directory, two synchronized views: folders and ordinary files on the left, archive files on the right. Directories named with an archive extension remain directories.

## Launch
Double-click launch.bat on KHzz. It uses the existing Python 3.14 / PySide6 installation. On another PC, install requirements.txt with pip and run python main.py.

## Features
- This PC drive root and lazy directory tree; quick access and persistent pinned folders.
- Shared background directory scan, independent sortable details views, live name filter, hidden-item toggle and file-system change monitoring.
- Address bar, back/forward/up, F5 refresh, persistent window size, position, maximized state and splitter positions. Restores the last browsed directory on launch; Home (Alt+Home) goes to This PC. If the saved directory is unavailable, starts at This PC.
- Copy, cut, paste, drag and drop, create folder, rename, recycle, open and copy path.
- ZIP creation and smart extraction: drag ZIPs from the archive pane into the file pane (or onto a folder). A single existing archive root is preserved; otherwise a folder named after the archive is created. A successful drag extraction recycles the original ZIP; extraction errors leave it intact. The context menu uses the same folder logic but retains the source. No overwrite, unsafe path and duplicate entry rejection; failed extraction cleans up staging. Other archives open using Windows file associations.
- File operations run on worker threads. Existing targets are not silently replaced. Moves and recycling ask for confirmation. No permanent-delete fallback when recycling fails.
- Classification is by case-insensitive file extension, including compound archive extensions. DOCX, XLSX and other ordinary packaged documents stay in the files pane.

![Preview Sample](https://i.imgur.com/uJk7Sjl.png)
## Tests
Run py -3.14 -B test_explorer.py. The test creates fixtures in a temporary directory and checks filtering, sorting, navigation, monitoring, transfers, and ZIP safety. No pre-existing files are modified.

## Scope
This is a filesystem explorer, not a replacement for the Windows Shell namespace or a full archive manager. Network access is subject to Windows permissions; non-ZIP extraction is delegated to the associated application. No elevated access is requested.

## Encrypted ZIPs
ZipCrypto and WinZip AES are supported. The first encrypted ZIP asks for a masked password. All later ZIPs, including later drag batches, try the last successful password automatically. Wrong passwords prompt again for that ZIP; Cancel skips it without deleting it. Plain ZIPs do not prompt or reset the password. The password is memory-only and is discarded at application exit. Extraction is queued so only one password prompt is active at a time.

## Network recycle
Windows SMB/UNC locations use a share-root .TriExplorer-Recycle directory instead of the local Recycle Bin. Each item is renamed into a unique directory on the same share, with its original relative path recorded. The Restore toolbar action restores a selected item to its original location without overwriting. The directory is omitted from normal TriExplorer listings. It is not a Windows system Recycle Bin, and nothing is automatically purged. If the share root is not writable, recycling fails without deleting the source. Local drives still use the native Recycle Bin.

## Quick Access
Right-click any folder in the directory tree or files pane to pin or unpin it. Right-click any Quick Access shortcut (including defaults) to remove it, or use Add pinned folder to browse. Removed default shortcuts stay removed across launches; Restore default shortcuts is available from the Quick Access menu. Unpinning never modifies the filesystem.

## Independent pane views
Each content pane has its own view selector: the original Files & folders (excluding archives), All items (folders first), All items A–Z (intermixed), Folders only, Files only (including archives), Compressed archives, Images, Documents, Videos, Audio and Code & scripts. Both panes stay on the same directory. Switching presets is instant using the existing shared directory model; column sort and view selection persist on normal exit. Dragging an archive selection between panes still extracts ZIPs, regardless of the current view preset.

## Exact duplicates (SHA-256)
Select Duplicates only in either pane to inspect exact matches in the current folder. Right-click in a content pane and open Duplicates (SHA-256) to find duplicates, recycle all but one per group, or explicitly scan recursively (confirmation required). Right-click a file and choose Keep this file, recycle matching copies to preserve that exact file. Automatic selection prefers names without copy suffixes, then oldest creation time, then path. Files are grouped by size before hashing; singletons are not read. Hidden items follow the Hidden items setting; directory junctions, symlinks, system recycle directories and extraction staging are skipped. Scans run in workers, and file identity/metadata is rechecked before recycling. Network duplicates go to the share-local recycle folder; no permanent deletion fallback.

## Folder size cache
Folder rows show recursive logical file-byte totals in the Size column, sortable like file sizes. Cached values load from a local SQLite database under %LOCALAPPDATA%/TriExplorer on the machine running the application (not on the SMB share). Missing or stale values are refreshed in a background worker, one folder at a time; completed subdirectory sizes are also saved. A tilde indicates a stale cached value; hover to see the calculation timestamp. A cache entry is stale after seven days or a detected direct directory timestamp change. Changes deep inside a tree or in-place file edits may not update parent timestamps; use Recalculate folder sizes or Recalculate this folder size from the context menu to force a fresh calculation. No whole-drive traversal at This PC. Links/junctions, internal recycle and extraction staging are excluded; inaccessible or incomplete trees do not replace cached totals. This is logical content size, not actual allocated disk clusters or compressed storage usage.

Duplicate scans display SHA candidate progress directly in the pane header, with skipped-file details in the right-click Duplicate menu. Windows/SMB may represent file IDs differently across APIs, so stability is verified independently through path and open-handle metadata. Numbered copy suffixes such as _2 are deprioritized. Drag ZIPs in either direction between content panes, regardless of the view presets: blank space extracts to the current folder; a folder row extracts inside that folder. Mixed archive/nonarchive drags between panes are rejected to avoid unintended moves. No user files are modified by the regression suite.

Quick Access and the directory tree are separated by a draggable vertical splitter; its position is saved on exit. SHA scanning reports smaller completed groups first, avoids repeated scans on unchanged folder notifications, and pauses uncached folder-size work while hashing. Right-click a file for the direct Keep this file, recycle SHA-256 duplicates action.

SHA duplicate browsing uses exact byte-size buckets, three 64 KiB samples for larger files, then full SHA-256 only when sample signatures match. Full hashes are cached locally for seven days against path/file identity/size/timestamps; cleanup always hashes fresh. ZIP / folder matches is a separate view and context-menu command. Cached folder sizes shortlist ZIPs approximately 80–100% as large (small ZIP header allowance); matching names also qualify regardless of compression. The central directory's uncompressed lengths and normalized paths must match the folder, including empty directories, allowing one wrapper folder. Matching members are streamed and compared via SHA-256 without extraction. Encrypted ZIPs need a password for verified matches; path/size-only candidates are explicitly marked unverified. Reuses the session password or accepts one via Verify ZIP / folder matches with password. Archive/folder comparisons are read-only and are deliberately excluded from automatic SHA duplicate recycling.

ZIP / folder matches defaults to alphabetical order without grouping by type. Its context menu offers Recycle matching ZIPs - keep folders, which re-verifies each currently listed pair with fresh SHA-256 checks, reuses the last successful ZIP password and prompts when needed. Only the archive is recycled; skipped, mismatched or unverified pairs are retained. Ordinary SHA duplicate cleanup remains separate. The matching view is refreshed after cleanup.
\nDouble-click files to rename inline (the filename stem is preselected). Enter commits and starts renaming the next file in the original visible order; Escape or focus loss ends the sequence. Existing names are never overwritten. Folders still open with double-click, and files can be opened via right-click Open or Enter when not editing. The Image preview toolbar button (Alt+P) opens a resizable fourth panel. It previews the selected image only, debounces selection changes, bounds the decoded preview size, and releases the image and cancels pending work when hidden. No thumbnail scan is performed. Supported formats depend on installed Qt image plugins.\n
## Portable configuration and pane module

The explorer reads and writes settings.ini beside main.py, not the Windows registry.
A first run copies any missing old registry settings and initializes defaults.
See settings.example.ini for the editable [app] name, [cache] sqlite_path,
and [network] trash_name options. Auto SQLite uses each machine's local
%LOCALAPPDATA%/TriExplorer/folder-sizes.sqlite3 (the SHA cache shares this DB).
Relative SQLite paths resolve against the application folder, not the PyCharm
working directory. Network trash is a single folder name at the root of each
SMB share; changing it does not move prior recycled items.

panels.py owns the file-row model, filter proxy, table widget, inline rename
field, image-preview label, and create_table_pane factory. filters.py is
the single source of view presets and file-extension categories. main.py
coordinates navigation, filesystem operations, and background work.
Local settings.ini is intentionally gitignored; settings.example.ini is versioned.

## Source layout

For surgical edits, see [ARCHITECTURE.md](ARCHITECTURE.md). The eight-line main.py starts the app, explorer.py owns the window, controllers/ handles UI features, and services/ performs filesystem operations. Source SVG and Windows taskbar icon are in assets/.

## Portable Windows release

Download TriExplorer-v0.1.0-win64.zip from Releases and extract into a
writable directory. Keep these three files together:

- TriExplorer.exe: self-contained PyInstaller one-file Windows executable
  (no Python installation required; Qt assets packaged internally)
- settings.ini: user-editable configuration, window state and pane settings
- TriExplorer.sqlite3: local folder-size and SHA cache (starts empty)

The frozen app resolves settings and relative SQLite paths beside the EXE,
not AppData, the working directory or PyInstaller temporary extraction.
The frozen build never migrates registry settings. Network recycle remains
on the source share and is configured via network/trash_name in settings.ini.

Windows build command (requires requirements.txt plus PyInstaller):
    py -3.14 -B build_portable.py --version 0.1.0

The GitHub Actions release workflow runs the same build and regression suite.

## Debug mode (automatic refresh disabled)

Run `python main-debugmode.py` from the source checkout, or use
`python main-debugmode.py --settings "C:/my-TOOLS/TriExplorer/settings.ini" --folder "Z:/problem-folder"`.
The startup console prints the local session log path. Filesystem watcher
notifications are logged but never trigger a reload. F5 and normal file
operations still refresh. Logs include refresh callers, worker queue/run
times, scan counts, model changes, and UI stalls. Debug mode uses a copy of
settings.ini and its own empty SQLite cache in a local temporary session
directory; the normal configuration and cache are not modified.

## Recycle Bin and portable settings

Quick Access includes a Recycle Bin beneath This PC. The combined view shows Windows Shell and discovered network-share recycle entries, with a source column and filter. Restore returns selected items to their original locations; permanent deletion requires confirmation. Unavailable shares are reported, not treated as empty.

Settings remain beside the application. Qt serializes a local staging copy, then a nonblocking OS write guard and atomic replace publish only modified keys to the portable INI. Legacy Qt locks are never removed automatically; a busy or stale lock produces a warning instead of waiting recursively. Older builds should not be used concurrently with the new writer. Startup checks the last location in a worker.

## Status history

Click Status history in the bottom status bar or press Alt+H to expand a resizable terminal-style history of the latest 500 nonempty status messages. Messages are timestamped and collected even when collapsed. Copy all and Clear operate on the session history only; nothing is written to disk.

## Archive containment (ZIP and ISO)

Select ZIP / ISO / folder matches, then enable Include contained archives at the bottom-right of that pane to allow extra files in the destination folder. Each pane has an independent toggle; cleanup uses the options of the invoking pane. The 70% size-multiset screen counts archive members, not compressed bytes; candidates must then match every member path and size, followed by full SHA-256 verification. Recycle matching archives re-verifies fresh content and never removes the folder. Encrypted ZIPs require a valid password. ISO filesystem contents are streamed via pycdlib without mounting. Bootable/hybrid ISOs, nonempty system areas, and images with unverifiable alternate filesystem trees are retained even if their visible file contents match. ISO filesystem equivalence is not bit-for-bit disk-image equivalence.

## Folder contents and external navigation

Folders only and Empty folders display a sortable Contents column (F = immediate files, D = immediate directories; other = links or special entries). Counts include hidden items and excluded directories; Size remains the recursive logical file total with the existing exclusions. Counts share the folder-size cache, upgraded in place. Unknown and stale counts are excluded from Empty folders; use Recalculate folder sizes to refresh cached results. Alt+double-click opens the actual folder in Windows Explorer without navigating TriExplorer.
