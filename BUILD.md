# Önálló alkalmazás építése (.exe / .app)

Az appot a **PyInstaller** csomagolja egyetlen, dupla kattintással indítható
alkalmazássá. A nehézséget a **Playwright Chromium** böngésző becsomagolása
jelenti — ezt a build scriptek kezelik.

> **Platform!** A PyInstaller mindig arra az OS-re épít, amelyiken futtatod:
> - **Windows `.exe`** → Windowson kell buildelni (`build_app.bat`).
> - **macOS `.app`** → macOS-en (`build_app.sh`).
> - **Linux bináris** → Linuxon (`build_app.sh`).
>
> Keresztfordítás nem lehetséges (macOS-en NEM készíthető `.exe`).

## Windows (.exe)

Windows gépen, a projekt mappájában:

```bat
build_app.bat
```

Eredmény: `dist\TSRSims4ModDownloader\TSRSims4ModDownloader.exe` — ezt a
**teljes `TSRSims4ModDownloader` mappát** kell átadni/másolni (a böngésző is
benne van), és az `.exe` rögtön indítható.

## macOS / Linux (.app / bináris)

```bash
./build_app.sh
```

Eredmény macOS-en: `dist/TSRSims4ModDownloader.app` (dupla kattintással indul).

## Mit csinálnak a scriptek

1. Telepítik a függőségeket + a `pyinstaller`-t.
2. A Chromiumot egy helyi `ms-playwright/` mappába töltik
   (`PLAYWRIGHT_BROWSERS_PATH`), hogy be tudják csomagolni.
3. PyInstaller a következő kulcs-kapcsolókkal:
   - `--windowed` – ne nyisson konzolablakot,
   - `--collect-all playwright` – a Playwright driver/csomag becsomagolása,
   - `--add-data ms-playwright` – a Chromium az app mellé kerül.
4. Futásidőben az app (`tsr/downloader.py::_configure_bundled_browsers`)
   frozen módban automatikusan megtalálja a mellécsomagolt böngészőt.

## Megjegyzések / hibaelhárítás

- **Méret:** a Chromium miatt a kész csomag ~150–300 MB. Ez normális.
- **macOS Gatekeeper:** aláíratlan `.app`-nál első indításkor jobb klikk →
  „Megnyitás", vagy: `xattr -dr com.apple.quarantine dist/TSRSims4ModDownloader.app`.
- **Egyetlen fájl (`--onefile`):** működhet, de Playwright + onefile lassabb
  indulású és törékenyebb; a mappás (onedir) kimenet a megbízhatóbb — ezt
  használják a scriptek.
- **Ha mégsem indul a böngésző** a kész csomagban: ellenőrizd, hogy az
  `ms-playwright/` mappa bekerült-e az app mellé, ill. hogy a build során
  lefutott-e a `playwright install chromium`.
```
