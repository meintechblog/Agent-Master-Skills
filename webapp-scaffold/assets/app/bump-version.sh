#!/usr/bin/env bash
# bump-version.sh — tooling-freier Semver-Bump nach Conventional Commits.
# Generiert von webapp-scaffold. Einheitliche Versionslogik
# (Semver-Konvention): package.json "version" ist die EINE kanonische
# Quelle; pro Release: Version bumpen + CHANGELOG-Eintrag + Git-Tag vX.Y.Z.
#
# Bump-Regel (aus den Commits SEIT dem letzten Tag, höchster gewinnt):
#   feat!:/ "BREAKING CHANGE:"  → MAJOR   (Pre-1.0: → MINOR, 1.0.0 nur bewusst via --bump major)
#   feat:                       → MINOR
#   fix:/perf:                  → PATCH
#   docs/chore/refactor/test/style/ci/build → kein Bump
#
# Verzahnt mit webapp-auto-updater: der Tag IST die Version; ein GitHub-Release am
# main-HEAD (`gh release create vX.Y.Z --target main`) triggert das Update aller Instanzen.
#
# Usage:
#   bash scripts/bump-version.sh                 # Auto-Bump aus Commits seit letztem Tag
#   bash scripts/bump-version.sh --bump minor    # Bump-Art erzwingen
#   bash scripts/bump-version.sh --dry-run       # nur anzeigen, nichts schreiben
#   bash scripts/bump-version.sh --no-tag        # Version+CHANGELOG, aber kein Git-Tag/Commit
#
# Danach:  git push --follow-tags   (und optional: gh release create vX.Y.Z --target main)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

FORCE_BUMP="" DRY=0 NO_TAG=0
while [ $# -gt 0 ]; do case "$1" in
  --bump) FORCE_BUMP="$2"; shift 2;;
  --dry-run) DRY=1; shift;;
  --no-tag) NO_TAG=1; shift;;
  *) echo "unknown arg: $1"; exit 1;;
esac; done

read_version() { node -e 'process.stdout.write(require("./package.json").version)'; }
write_version() { node -e 'const fs=require("fs"),p="./package.json",j=JSON.parse(fs.readFileSync(p));j.version=process.argv[1];fs.writeFileSync(p,JSON.stringify(j,null,2)+"\n")' "$1"; }

CUR="$(read_version)"
IFS='.' read -r MA MI PA <<<"$CUR"

# Commits seit dem letzten Tag einsammeln (oder alle, wenn noch kein Tag).
LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
if [ -n "$LAST_TAG" ]; then RANGE="$LAST_TAG..HEAD"; else RANGE="HEAD"; fi
SUBJECTS="$(git log --format='%s%n%b' "$RANGE" 2>/dev/null || true)"

# Bump-Art bestimmen.
BUMP="none"
if [ -n "$FORCE_BUMP" ]; then
  BUMP="$FORCE_BUMP"
else
  if echo "$SUBJECTS" | grep -qE '^[a-z]+(\([^)]*\))?!:|BREAKING CHANGE'; then BUMP="major";
  elif echo "$SUBJECTS" | grep -qE '^feat(\([^)]*\))?:'; then BUMP="minor";
  elif echo "$SUBJECTS" | grep -qE '^(fix|perf)(\([^)]*\))?:'; then BUMP="patch";
  fi
fi

if [ "$BUMP" = "none" ]; then
  echo "Kein release-relevanter Commit seit ${LAST_TAG:-Beginn} (nur docs/chore/…). Kein Bump."
  exit 0
fi

# Pre-1.0: breaking wird zu minor heruntergestuft (1.0.0 nur bewusst via --bump major).
if [ "$MA" = "0" ] && [ "$BUMP" = "major" ] && [ -z "$FORCE_BUMP" ]; then
  echo "Pre-1.0: 'breaking' → MINOR (1.0.0 nur bewusst: --bump major)."
  BUMP="minor"
fi

case "$BUMP" in
  major) MA=$((MA+1)); MI=0; PA=0;;
  minor) MI=$((MI+1)); PA=0;;
  patch) PA=$((PA+1));;
  *) echo "ungültiger --bump: $BUMP (major|minor|patch)"; exit 1;;
esac
NEW="$MA.$MI.$PA"
DATE="$(date +%F)"

echo "Version: $CUR → $NEW  (bump=$BUMP, $(echo "$SUBJECTS" | grep -cE '^(feat|fix|perf)' || true) relevante Commits)"
[ "$DRY" = 1 ] && { echo "(dry-run — nichts geschrieben)"; exit 0; }

write_version "$NEW"

# CHANGELOG-Eintrag aus den relevanten Commits erzeugen (Keep-a-Changelog).
CHANGES="$(echo "$SUBJECTS" | grep -E '^(feat|fix|perf)(\([^)]*\))?!?:' | sed -E 's/^/- /' || true)"
[ -z "$CHANGES" ] && CHANGES="- $BUMP-Release"
if [ -f CHANGELOG.md ]; then
  TMP="$(mktemp)"
  CHGFILE="$(mktemp)"
  # CHANGES ist mehrzeilig (eine Zeile pro Commit). awk -v kann KEINE literalen
  # Newlines im Wert verarbeiten ("awk: newline in string") → über Datei + getline
  # einlesen statt via -v, sonst bleibt der CHANGELOG-Abschnitt bei >1 Commit leer.
  printf '%s\n' "$CHANGES" > "$CHGFILE"
  awk -v ver="$NEW" -v date="$DATE" -v chgfile="$CHGFILE" '
    BEGIN{done=0}
    /^## \[Unreleased\]/ && !done { print; print ""; print "## [" ver "] - " date; while((getline line < chgfile) > 0) print line; print ""; done=1; next }
    { print }
    END{ if(!done){ print "## [" ver "] - " date; while((getline line < chgfile) > 0) print line } }
  ' CHANGELOG.md > "$TMP" && mv "$TMP" CHANGELOG.md
  rm -f "$CHGFILE"
else
  printf '# Changelog\n\n## [Unreleased]\n\n## [%s] - %s\n%s\n' "$NEW" "$DATE" "$CHANGES" > CHANGELOG.md
fi

if [ "$NO_TAG" = 1 ]; then
  echo "package.json + CHANGELOG.md aktualisiert (kein Tag, --no-tag)."
  exit 0
fi

git add package.json CHANGELOG.md
git commit -q -m "chore(release): v$NEW"
git tag "v$NEW"
echo "✓ v$NEW committed + getaggt."
echo "  Nächster Schritt:  git push --follow-tags"
echo "  Update ausrollen:   gh release create v$NEW --target main --title v$NEW --notes-from-tag"
