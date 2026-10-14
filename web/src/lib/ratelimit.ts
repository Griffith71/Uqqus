import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

const redis = new Redis({
  url: process.env.UPSTASH_REDIS_REST_URL!,
  token: process.env.UPSTASH_REDIS_REST_TOKEN!,
});

/**
 * Creates a sliding-window rate limiter scoped by `prefix`.
 * Use one instance per write endpoint, e.g. createRateLimiter("post:create", 5, "1 m").
 */
export function createRateLimiter(
  prefix: string,
  limit: number,
  window: `${number} ${"ms" | "s" | "m" | "h" | "d"}`,
) {
  return new Ratelimit({
    redis,
    limiter: Ratelimit.slidingWindow(limit, window),
    prefix: `ratelimit:${prefix}`,
    analytics: true,
  });
}
