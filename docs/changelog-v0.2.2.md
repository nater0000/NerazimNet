# NerazimNet v0.2.2

## Bug Fixes

- **Correct app icon restored.** v0.2.0 shipped the wrong glyph (a rounded chain-link icon). The window/tray icon, in-app logo, and macOS `.icns` are now rebuilt from the authentic pixel-art "N" asset.
- **Disabled dropdown/button text readable again.** `text_color_disabled` for buttons and option menus was a light gray intended for dark surfaces — on the lime-green fill it washed out (visible on the disabled "Server" dropdown). Disabled text now uses muted variants of the enabled color in both modes.
- Disabled option menus now display their placeholder ("No servers configured") instead of the default "CTkOptionMenu" label.
