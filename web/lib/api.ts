import "server-only";
import { djangoFetch } from "./server/djangoFetch";
import { readApiError, ApiClientError } from "./errors";
import type {
  AssetDetail,
  AssetList,
  BrandKit,
  Job,
  JobList,
  Me,
  OpsCatalogue,
  RecipeList,
  Usage,
} from "./types";

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) throw new ApiClientError(await readApiError(res));
  return res.json() as Promise<T>;
}

export interface ListJobsParams {
  status?: string;
  limit?: number;
  offset?: number;
}

export const api = {
  me: () => djangoFetch("/me").then((r) => parse<Me>(r)),

  ops: () => djangoFetch("/ops").then((r) => parse<OpsCatalogue>(r)),

  listAssets: (limit = 50, offset = 0) =>
    djangoFetch(`/assets?limit=${limit}&offset=${offset}`).then((r) => parse<AssetList>(r)),

  getAsset: (handle: string) =>
    djangoFetch(`/assets/${encodeURIComponent(handle)}`).then((r) => parse<AssetDetail>(r)),

  listJobs: ({ status, limit = 25, offset = 0 }: ListJobsParams) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) params.set("status", status);
    return djangoFetch(`/jobs?${params}`).then((r) => parse<JobList>(r));
  },

  getJob: (id: string) => djangoFetch(`/jobs/${encodeURIComponent(id)}`).then((r) => parse<Job>(r)),

  listRecipes: () => djangoFetch("/recipes").then((r) => parse<RecipeList>(r)),

  getUsage: () => djangoFetch("/usage").then((r) => parse<Usage>(r)),

  getBrandKit: () => djangoFetch("/brand-kit").then((r) => parse<BrandKit>(r)),
};
