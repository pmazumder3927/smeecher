export async function copyTextToClipboard(text) {
    const value = String(text ?? '');
    if (!value) return false;

    // Modern async clipboard API (requires secure context, works on localhost).
    try {
        if (navigator?.clipboard?.writeText) {
            await navigator.clipboard.writeText(value);
            return true;
        }
    } catch {
        // fall through to legacy copy
    }

    // Legacy fallback.
    try {
        const el = document.createElement('textarea');
        el.value = value;
        el.setAttribute('readonly', 'true');
        el.style.position = 'fixed';
        el.style.top = '0';
        el.style.left = '0';
        el.style.width = '2em';
        el.style.height = '2em';
        el.style.padding = '0';
        el.style.border = 'none';
        el.style.outline = 'none';
        el.style.boxShadow = 'none';
        el.style.background = 'transparent';
        document.body.appendChild(el);
        el.focus();
        el.select();

        const ok = document.execCommand('copy');
        document.body.removeChild(el);
        return ok;
    } catch {
        return false;
    }
}

