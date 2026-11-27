PANE_MODES = (
    ("default", "Files & folders"),
    ("all", "All items"),
    ("alphabetical", "All items · A–Z"),
    ("folders", "Folders only"),
    ("files", "Files only"),
    ("archives", "Compressed archives"),
    ("images", "Images"),
    ("documents", "Documents"),
    ("video", "Videos"),
    ("audio", "Audio"),
    ("code", "Code & scripts"),
    ("duplicates", "Duplicates only (SHA-256)"),
    ("archive_matches", "ZIP / ISO / folder matches"),
)
CATEGORY_SUFFIXES = {
    "images": frozenset(
        ".jpg .jpeg .png .gif .webp .bmp .tiff .tif .svg .ico .heic .heif .avif .jxl .raw .dng .psd .ai .cr2 .cr3 .nef .arw .orf .rw2".split()),
    "documents": frozenset(
        ".pdf .doc .docx .odt .rtf .txt .md .xls .xlsx .ods .csv .tsv .ppt .pptx .odp .epub .mobi".split()),
    "video": frozenset(".mp4 .mkv .avi .mov .wmv .webm .m4v .mpeg .mpg .mts .m2ts .flv .3gp".split()),
    "audio": frozenset(".mp3 .wav .flac .aac .m4a .ogg .opus .wma .aiff .aif .alac .mid .midi".split()),
    "code": frozenset(
        ".py .pyw .js .jsx .ts .tsx .mjs .cjs .html .css .scss .json .yaml .yml .toml .xml .sql .sh .bat .cmd .ps1 .c .h .cpp .hpp .cs .java .kt .go .rs .rb .php .swift .lua .r .vue .svelte .ipynb".split()),
    "archives": frozenset((".zip", ".7z", ".rar", ".tar", ".tgz", ".tbz", ".tbz2", ".txz", ".gz", ".bz2", ".xz", ".zst",
                          ".cab", ".arj", ".lz", ".lzma", ".iso", ".wim", ".esd", ".zipx", ".z", ".lzh", ".cpio",
                          ".001", ".tar.zst"))
}


# The archive suffixes are centrally defined here, including compound extensions.
# The longest suffix is considered first (e.g. ".tar.zst" before ".zst").
ARCHIVE_SUFFIXES = tuple(sorted(CATEGORY_SUFFIXES["archives"],
                                key=lambda suffix: (-len(suffix), suffix)))


def archive_name(name: str) -> bool:
    """Case-insensitive archive classification used by all panes and operations."""
    return name.casefold().endswith(ARCHIVE_SUFFIXES)
