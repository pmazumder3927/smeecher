import { writable, get } from "svelte/store";

const CDRAGON_BASE =
  "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default";

// Asset lookup maps
export const itemIconMap = writable(new Map());
export const itemDisplayMap = writable(new Map());
export const traitIconMap = writable(new Map());
export const traitDisplayMap = writable(new Map());
export const displayNameIndex = writable(new Map());
export const failedIcons = writable(new Set());

// Loading state
export const assetsLoaded = writable(false);

/**
 * Load TFT data from Community Dragon
 */
export async function loadCDragonData() {
  try {
    const [itemsRes, traitsRes] = await Promise.all([
      fetch(`${CDRAGON_BASE}/v1/tftitems.json`),
      fetch(`${CDRAGON_BASE}/v1/tfttraits.json`),
    ]);

    const [items, traits] = await Promise.all([
      itemsRes.json(),
      traitsRes.json(),
    ]);

    const newItemIconMap = new Map();
    const newItemDisplayMap = new Map();
    const newTraitIconMap = new Map();
    const newTraitDisplayMap = new Map();
    const newDisplayNameIndex = new Map();

    // Build item lookups
    items.forEach((item) => {
      const displayName = item.name?.replace(/<[^>]*>/g, "").trim();
      const nameId = item.nameId || "";
      const iconPath = item.squareIconPath || item.iconPath;

      if (displayName && iconPath) {
        const url = `${CDRAGON_BASE}${iconPath
          .toLowerCase()
          .replace("/lol-game-data/assets", "")}`;

        newItemIconMap.set(displayName.toLowerCase(), url);

        if (nameId) {
          newItemIconMap.set(nameId.toLowerCase(), url);
          newItemDisplayMap.set(nameId.toLowerCase(), displayName);

          const cleaned = nameId
            .replace(/^TFT\d*_Item_/i, "")
            .replace(/^TFT_Item_/i, "");
          if (cleaned !== nameId) {
            newItemIconMap.set(cleaned.toLowerCase(), url);
            newItemDisplayMap.set(cleaned.toLowerCase(), displayName);
          }

          newDisplayNameIndex.set(displayName.toLowerCase(), {
            apiName:
              cleaned !== nameId ? cleaned : nameId.replace(/^TFT_Item_/i, ""),
            type: "item",
            displayName: displayName,
          });
        }
      }
    });

    // Build trait lookups
    traits.forEach((trait) => {
      const displayName = trait.display_name;
      const traitId = trait.trait_id || "";
      const iconPath = trait.icon_path;

      if (displayName && iconPath) {
        const url = `${CDRAGON_BASE}${iconPath
          .toLowerCase()
          .replace("/lol-game-data/assets", "")}`;

        newTraitIconMap.set(displayName.toLowerCase(), url);

        if (traitId) {
          newTraitIconMap.set(traitId.toLowerCase(), url);
          newTraitDisplayMap.set(traitId.toLowerCase(), displayName);

          const cleaned = traitId
            .replace(/^TFT\d*_/i, "")
            .replace(/^Set\d*_/i, "");
          if (cleaned !== traitId) {
            newTraitIconMap.set(cleaned.toLowerCase(), url);
            newTraitDisplayMap.set(cleaned.toLowerCase(), displayName);
          }

          if (traitId.startsWith("TFT16_")) {
            newDisplayNameIndex.set(displayName.toLowerCase(), {
              apiName: cleaned,
              type: "trait",
              displayName: displayName,
            });
          }
        }
      }
    });

    // Update stores
    itemIconMap.set(newItemIconMap);
    itemDisplayMap.set(newItemDisplayMap);
    traitIconMap.set(newTraitIconMap);
    traitDisplayMap.set(newTraitDisplayMap);
    displayNameIndex.set(newDisplayNameIndex);
    assetsLoaded.set(true);

    console.log(
      `Loaded ${newItemIconMap.size} item mappings, ${newTraitIconMap.size} trait mappings`
    );
  } catch (error) {
    console.warn("Failed to load CDragon data:", error);
    assetsLoaded.set(true); // Continue anyway
  }
}

/**
 * Get icon URL for a given type and name
 */
export function getIconUrl(type, name) {
  const lower = name.toLowerCase();

  switch (type) {
    case "unit":
      return `${CDRAGON_BASE}/assets/characters/tft16_${lower}/hud/tft16_${lower}_square.tft_set16.png`;

    case "item": {
      const iconMap = get(itemIconMap);
      if (iconMap.has(lower)) {
        return iconMap.get(lower);
      }
      return `${CDRAGON_BASE}/assets/maps/tft/icons/items/hexcore/tft_item_${lower.replace(
        /['\s]/g,
        ""
      )}.tft_set13.png`;
    }

    case "trait": {
      const iconMap = get(traitIconMap);
      if (iconMap.has(lower)) {
        return iconMap.get(lower);
      }
      return `${CDRAGON_BASE}/assets/ux/traiticons/trait_icon_16_${lower.replace(
        /\s/g,
        ""
      )}.tft_set16.png`;
    }

    default:
      return null;
  }
}

/**
 * Get display name from API name
 */
export function getDisplayName(type, apiName) {
  const lower = apiName.toLowerCase();

  if (type === "item") {
    const displayMap = get(itemDisplayMap);
    if (displayMap.has(lower)) {
      return displayMap.get(lower);
    }
  }

  if (type === "trait") {
    const displayMap = get(traitDisplayMap);
    if (displayMap.has(lower)) {
      return displayMap.get(lower);
    }
  }

  return apiName;
}

/**
 * Mark an icon as failed to load
 */
export function markIconFailed(type, name) {
  failedIcons.update((set) => {
    const newSet = new Set(set);
    newSet.add(`${type}:${name}`);
    return newSet;
  });
}

/**
 * Check if an icon has failed to load
 */
export function hasIconFailed(type, name) {
  return get(failedIcons).has(`${type}:${name}`);
}
