# Support Companion

A local Python/Tkinter case manager with an optional mIRC handoff workflow. The application is deliberately site-neutral: site branding, IRC details, profile URL parsing, appearance, and case defaults live in `config.json` rather than the Python filename.

## Features

- Local case management with statuses: Started, Waiting, Ready, Submitted, and Closed.
- One JSON file per case, stored by status under `cases/`, plus a lightweight `cases/index.json`.
- Search and status filtering for larger case lists.
- Saved-value change tracking on the case form and Settings.
- Configurable profile-URL-to-profile-name extraction.
- Basic, Disabled, and Low Ratio message modes.
- Optional logo and favicon configuration.
- Light/Dark appearance support.
- mIRC handoff output.
- Automatic folder scaffolding and legacy single-file case migration.

## Requirements

- Windows with Python 3 and Tkinter available.
- mIRC is optional. It is only required if you use the mIRC handoff integration.

The application currently has no third-party Python package requirements.

## Suggested layout

```text
Support-Companion/
├─ support_companion.py
├─ config.json
├─ assets/
├─ handoff/
└─ cases/
   ├─ index.json
   ├─ started/
   ├─ waiting/
   ├─ ready/
   ├─ submitted/
   └─ closed/
```

Missing working folders are created automatically at startup.

## Install for Python-only use

1. Create a folder for the application.
2. Copy `support_companion.py` into it.
3. Copy `config.example.json` to `config.json` and edit it for your site, or let the application create its defaults and then use Settings.
4. Start the application with Python.

Example:

```bat
python support_companion.py --request-id test123 --mode basic --outdir handoff
```

Supported launch modes are `basic`, `disabled`, and `lowratio`.

## mIRC installation

A practical layout under the mIRC scripts directory is:

```text
%APPDATA%\mIRC\scripts\Support-Companion\
```

Place `support_companion.py` and `config.json` there. Configure the mIRC bridge/script to launch `support_companion.py` with the request ID, mode, and `handoff` output directory expected by your integration.

The Python application writes the handoff output locally. Keep user-specific paths in the local mIRC configuration rather than hard-coding them into the Python application.

## Configuration

`config.json` controls application branding and behavior. Important sections include:

- `app`: window title and sidebar subtitle.
- `irc`: IRC network, support channels, and allowed queries.
- `profiles`: allowed profile hosts and profile path prefix.
- `branding`: optional local/remote logo and favicon paths.
- `appearance`: default Light/Dark appearance.
- `case_manager`: default case filtering.

Logo and icon values may be blank. Branding is optional.

The Settings window provides an editor for these values.

## Case storage

New storage uses one file per case:

```text
cases/<status>/<case-id>.json
```

`cases/index.json` contains lightweight metadata used to populate and filter the case list.

If the older single `cases.json` file exists, the application can migrate its cases into the folder-based layout and preserve the old file as a migrated backup.

## GitHub repository notes

Before publishing:

- Do not commit real case data, handoff files, personal configuration, credentials, or private support information.
- Commit `config.example.json`, not a personal `config.json`.
- Add a license before presenting the project as open source.
- Review site logos and other branding assets separately before redistributing them.

See `.gitignore` in this repository for the local/runtime files that should stay out of source control.

## Troubleshooting

### The application does not start

Run it from Command Prompt so Python prints the traceback. Confirm the Python executable and application path are the ones you expect.

### A case will not save

Check that the application directory is writable. Case files are stored below `cases/` and the index is stored at `cases/index.json`.

### Branding is missing

Branding is optional. Check the paths/URLs in Settings or leave the branding fields blank.

### Profile name is not extracted

Check the allowed profile hosts and path prefix in Settings. The profile must match those configured values before the application auto-fills the profile name.

## Development

A quick syntax check is:

```bat
python -m py_compile support_companion.py
```

When changing storage code, test creating a case, changing its status, reopening it, and rebuilding the case list before publishing a release.
