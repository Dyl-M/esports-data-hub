"""Personal brand shown on shared visuals: name, profile picture and social accounts.

Files live in `paths.ASSETS_DIR`, kept out of git (local only). The social icons are Simple Icons marks (CC0,
simple-icons 13.21.0) and the font is JetBrains Mono (SIL Open Font License, `fonts/JetBrainsMono-OFL.txt`).
"""

BRAND_NAME = 'Esports Data Hub'
AVATAR_FILE = 'avatar.png'  # the author's profile picture, transparent background
FONT_FILE = 'fonts/JetBrainsMono-wght.ttf'  # variable weight font (100-800)

# (icon file stem in `icons/`, handle shown on the visual), in display order
SOCIALS: tuple[tuple[str, str], ...] = (
    ('github', 'Dyl-M'),
    ('reddit', 'u/Dyl_M'),
    ('bluesky', 'dyl-m.bsky.social'),
)
