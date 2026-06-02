#!/usr/bin/env bash
# webapp-scaffold — generiert ein generisches Next.js-Web-App-Scaffold (LXC-Deploy-Pattern:
# systemd + nginx Port 80) mit OPTIONALEM Wissensdatenbank-Modul (pgvector Hybrid-RRF + e5).
#
# Beispiel:
#   bash scaffold.sh --name foo-master --target ~/codex/foo-master \
#        --title "Foo Wissensdatenbank" --desc "Alles über Foo." --with-kb
#   bash scaffold.sh --name bar-master --target ~/codex/bar-master --title "Bar Tool"   # ohne KB
#   bash scaffold.sh --name baz-master --title "Baz" --with-github --github-repo <your-org>/<your-app>
#
# Danach: cd <target>; pnpm install; (KB: DB+Embedding-Service lokal, dann ingest); pnpm dev
# Deploy auf LXC: git push → clone nach /opt/<name> → bash scripts/deploy.sh
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KB_SKILL="${KB_SKILL_DIR:-$HOME/.claude/skills/knowledge-base/assets}"

# WICHTIG: 8765 wird häufig vom lokalen Speech/TTS-Service (Piper/whisper) belegt.
# Der Embedding-Service darf NIE auf 8765 laufen, sonst kollidiert er mit der lokalen Sprachausgabe.
NAME="" TARGET="" TITLE="" DESC="" PORT="3000" EMBED_PORT="8770" DB_NAME="" DB_USER="" WITH_KB=0 WITH_GITHUB=0 GITHUB_REPO=""
while [ $# -gt 0 ]; do case "$1" in
  --name) NAME="$2"; shift 2;;
  --target) TARGET="$2"; shift 2;;
  --title) TITLE="$2"; shift 2;;
  --desc) DESC="$2"; shift 2;;
  --port) PORT="$2"; shift 2;;
  --embed-port) EMBED_PORT="$2"; shift 2;;
  --db-name) DB_NAME="$2"; shift 2;;
  --db-user) DB_USER="$2"; shift 2;;
  --with-kb) WITH_KB=1; shift;;
  --with-github) WITH_GITHUB=1; shift;;
  --github-repo) GITHUB_REPO="$2"; shift 2;;
  *) echo "unknown arg: $1"; exit 1;;
esac; done
[ "$EMBED_PORT" = "8765" ] && { echo "ERROR: --embed-port 8765 ist häufig vom Speech-Service belegt. Waehle z.B. 8770."; exit 1; }

[ -z "$NAME" ] && { echo "ERROR: --name required"; exit 1; }
[ -z "$TARGET" ] && TARGET="$HOME/codex/$NAME"
[ -z "$TITLE" ] && TITLE="$NAME"
[ -z "$DESC" ] && DESC="$TITLE"
[ -z "$DB_NAME" ] && DB_NAME="$(echo "$NAME" | tr -cd 'a-z0-9_')"
[ -z "$DB_USER" ] && DB_USER="$DB_NAME"
ENV_PREFIX="$(echo "$NAME" | tr 'a-z-' 'A-Z_')"
[ -z "$GITHUB_REPO" ] && GITHUB_REPO="<your-org>/$NAME"   # Default; via --github-repo overridable

# Header-Nav-Links (JSX): leer per Default, GitHub-Link wenn --with-github.
NAV_LINKS=""
[ "$WITH_GITHUB" = "1" ] && NAV_LINKS='<Link href="/github" style={{ color: "var(--muted)", textDecoration: "none" }}>GitHub</Link>'

echo "==> Scaffold $NAME → $TARGET (KB=$WITH_KB, GitHub=$WITH_GITHUB, port=$PORT)"
mkdir -p "$TARGET"/{src/app,src/lib,src/components,scripts,.planning}

# sed-Substitution-Helper (alle Platzhalter)
# WICHTIG: Auf der sed-REPLACEMENT-Seite sind &, | und \ Sonderzeichen
# (& = gesamter Match). Freie Textfelder (--title/--desc) können diese enthalten
# (z.B. "Steuer & Buchhaltung") → vor dem Einsetzen escapen, sonst bleibt der
# Platzhalter kaputt stehen ("Steuer __APP_TITLE__ Buchhaltung").
esc() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
subst() {
  sed -e "s|__APP_NAME__|$NAME|g" \
      -e "s|__APP_TITLE__|$(esc "$TITLE")|g" \
      -e "s|__APP_DESC__|$(esc "$DESC")|g" \
      -e "s|__PORT__|$PORT|g" \
      -e "s|__DB_NAME__|$DB_NAME|g" \
      -e "s|__DB_USER__|$DB_USER|g" \
      -e "s|__WITH_KB__|$WITH_KB|g" \
      -e "s|__EMBED_PORT__|$EMBED_PORT|g" \
      -e "s|__DATE__|$(date +%F)|g" \
      -e "s|__NAV_LINKS__|$(esc "$NAV_LINKS")|g" \
      -e "s|__APP_ENV__|$ENV_PREFIX|g" "$1"
}

# --- Always: shell + config ---
subst "$SKILL_DIR/app/layout.tsx.tmpl"  > "$TARGET/src/app/layout.tsx"
cp     "$SKILL_DIR/app/globals.css"       "$TARGET/src/app/globals.css"
subst "$SKILL_DIR/app/package.json.tmpl" > "$TARGET/package.json"
cp     "$SKILL_DIR/app/tsconfig.json"     "$TARGET/tsconfig.json"
cp     "$SKILL_DIR/app/next.config.ts"    "$TARGET/next.config.ts"
subst "$SKILL_DIR/gitignore.tmpl"        > "$TARGET/.gitignore"

# --- Versionslogik (Versioning-Konvention) ---
# package.json "version" = kanonische Quelle (startet 0.1.0). CHANGELOG (Keep-a-Changelog)
# + tooling-freier Bump-Helper (Conventional Commits → semver + Git-Tag).
subst "$SKILL_DIR/app/CHANGELOG.md.tmpl" > "$TARGET/CHANGELOG.md"
cp     "$SKILL_DIR/app/bump-version.sh"   "$TARGET/scripts/bump-version.sh"
chmod +x "$TARGET/scripts/bump-version.sh"

# --- Deploy layer (always) ---
subst "$SKILL_DIR/deploy/deploy.sh.tmpl"       > "$TARGET/scripts/deploy.sh"
subst "$SKILL_DIR/deploy/app.service.tmpl"     > "$TARGET/scripts/$NAME.service"
subst "$SKILL_DIR/deploy/nginx.conf.tmpl"      > "$TARGET/scripts/nginx-$NAME.conf"
chmod +x "$TARGET/scripts/deploy.sh"

if [ "$WITH_KB" = "1" ]; then
  echo "==> + KB-Modul (pgvector Hybrid-Suche)"
  # Web-Layer (dieser Skill)
  cp "$SKILL_DIR/db/db.ts"                  "$TARGET/src/lib/db.ts"
  cp "$SKILL_DIR/db/categories.ts"          "$TARGET/src/lib/categories.ts"
  cp "$SKILL_DIR/db/embed.ts"               "$TARGET/src/lib/embed.ts"
  cp "$SKILL_DIR/app/SearchBox.tsx"         "$TARGET/src/components/SearchBox.tsx"
  mkdir -p "$TARGET/src/app/api/search" "$TARGET/src/app/doc/[slug]"
  cp "$SKILL_DIR/app/api-search-route.ts"   "$TARGET/src/app/api/search/route.ts"
  cp "$SKILL_DIR/app/doc-page.tsx.tmpl"     "$TARGET/src/app/doc/[slug]/page.tsx"
  subst "$SKILL_DIR/app/page-kb.tsx.tmpl" > "$TARGET/src/app/page.tsx"
  # KB-Pipeline (DRY: aus knowledge-base-Skill — NICHT duplizieren)
  if [ -d "$KB_SKILL" ]; then
    cp "$KB_SKILL/schema.sql"           "$TARGET/scripts/schema.sql"
    cp "$KB_SKILL/ingest.py"            "$TARGET/scripts/ingest.py"
    cp "$KB_SKILL/embedding-service.py" "$TARGET/scripts/embedding-service.py"
    echo "    ⚠ scripts/schema.sql + ingest.py: source_type/category-Enums (TODO) für deine Domäne ausfüllen!"
  else
    echo "    ⚠ knowledge-base-Skill nicht gefunden ($KB_SKILL) — schema.sql/ingest.py/embedding-service.py manuell ergänzen."
  fi
  printf 'fastapi\nuvicorn\nsentence-transformers>=3\npsycopg[binary]\nhttpx\npyyaml\npypdf\n' > "$TARGET/scripts/requirements.txt"
  subst "$SKILL_DIR/deploy/embedding.service.tmpl" > "$TARGET/scripts/$NAME-embedding.service"
  mkdir -p "$TARGET/content"
  echo "# content/ — KB-Artikel nach knowledge-base-Standard (.md mit Frontmatter)" > "$TARGET/content/README.md"
else
  subst "$SKILL_DIR/app/page-plain.tsx.tmpl" > "$TARGET/src/app/page.tsx"
fi

if [ "$WITH_GITHUB" = "1" ]; then
  echo "==> + GitHub-Modul (offene Issues + PRs des eigenen Repos, REST-API, single-repo read-only)"
  cp "$SKILL_DIR/app/github.ts"            "$TARGET/src/lib/github.ts"
  mkdir -p "$TARGET/src/app/api/github" "$TARGET/src/app/github"
  cp "$SKILL_DIR/app/api-github-route.ts"  "$TARGET/src/app/api/github/route.ts"
  subst "$SKILL_DIR/app/github-page.tsx.tmpl" > "$TARGET/src/app/github/page.tsx"
  # ENV-Doku (Secrets NICHT committen — .env.example, .env.local lädt Next.js automatisch)
  cat > "$TARGET/.env.example" <<EOF
# GitHub-Modul (--with-github): in .env.local kopieren (gitignored) + ausfüllen.
GITHUB_REPO=$GITHUB_REPO
# PAT mit repo-Scope — PFLICHT für private Repos (ohne: nur public, 60 req/h):
GITHUB_TOKEN=
EOF
  echo "    ⚠ GitHub: .env.example → .env.local kopieren + GITHUB_TOKEN setzen (private Repos)."
fi

cat > "$TARGET/.planning/RESUME.md" <<EOF
# $NAME — Resume
Scaffold via webapp-scaffold (KB=$WITH_KB). Internes App-Port: $PORT, nginx → 80.
$( [ "$WITH_KB" = 1 ] && echo "DB: $DB_NAME / Rolle $DB_USER. KB-Enums in scripts/schema.sql + ingest.py ausfüllen, content/ befüllen, ingesten." )
$( [ "$WITH_GITHUB" = 1 ] && echo "GitHub-Modul: .env.example → .env.local (GITHUB_REPO=$GITHUB_REPO, GITHUB_TOKEN für private Repos). /github + /api/github." )
EOF

echo "==> Fertig: $TARGET"
echo "   Next: cd $TARGET && pnpm install && pnpm build"
[ "$WITH_KB" = 1 ] && echo "   KB: Enums in scripts/schema.sql + ingest.py füllen, content/ befüllen → ingest.py --prune"
[ "$WITH_GITHUB" = 1 ] && echo "   GitHub: cp .env.example .env.local; GITHUB_TOKEN setzen (private Repos) → /github"
