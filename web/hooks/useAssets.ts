"use client";

import { useEffect, useState } from "react";
import type { Asset } from "@/lib/types";

/** A small client-side list fetch for a single open AssetPicker. Not a general-purpose cache:
 * at most one picker is open at a time, so there is nothing worth sharing between instances. */
export function useAssets() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // loading already starts true (useState above); no need to set it again here.
    fetch("/api/proxy/assets?limit=100")
      .then((res) => {
        if (!res.ok) throw new Error("Could not load assets.");
        return res.json();
      })
      .then((body: { items: Asset[] }) => {
        if (!cancelled) setAssets(body.items);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { assets, loading, error };
}
