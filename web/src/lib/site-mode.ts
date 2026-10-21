export const staticDemo = process.env.NEXT_PUBLIC_STATIC_DEMO === "true";
export const sitePath = (path: string) => `${staticDemo ? (process.env.NEXT_PUBLIC_BASE_PATH || "/museum_agent") : ""}${path.startsWith("/") ? path : `/${path}`}`;
