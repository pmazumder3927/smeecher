#!/bin/bash
# Upload data files to Railway server

SERVER="https://smeecher.up.railway.app"
SECRET="${UPLOAD_SECRET:-}"

if [ -z "$SECRET" ]; then
    echo "Usage: UPLOAD_SECRET=xxx ./upload.sh [engine|db|both]"
    exit 1
fi

upload_file() {
    local file=$1
    local name=$2

    if [ ! -f "$file" ]; then
        echo "Error: $file not found"
        return 1
    fi

    size=$(du -h "$file" | cut -f1)
    echo "Uploading $name ($size)..."

    curl -X PUT "$SERVER/upload-data/$name" \
        -H "X-Upload-Secret: $SECRET" \
        --data-binary "@$file" \
        --max-time 600 \
        --connect-timeout 30 \
        --progress-bar | cat
    echo ""
}

DATA_DIR="$(dirname "$0")/data"
MODE="${1:-both}"

case "$MODE" in
    engine)
        upload_file "$DATA_DIR/engine.bin" "engine.bin"
        ;;
    db)
        upload_file "$DATA_DIR/smeecher.db" "smeecher.db"
        ;;
    both)
        upload_file "$DATA_DIR/engine.bin" "engine.bin"
        upload_file "$DATA_DIR/smeecher.db" "smeecher.db"
        ;;
    *)
        echo "Usage: UPLOAD_SECRET=xxx ./upload.sh [engine|db|both]"
        exit 1
        ;;
esac

echo "Done!"
