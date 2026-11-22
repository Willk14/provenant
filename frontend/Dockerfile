# Provenant web app (Next.js standalone server).
# PROVENANT_API_URL is where the server forwards /api: Next resolves rewrites at build time, so it is a build
# argument, one image per environment (e.g. http://api:8000 in compose, the API's internal URL when hosted).
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
COPY scripts ./scripts
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
ARG PROVENANT_API_URL=http://api:8000
ENV PROVENANT_API_URL=$PROVENANT_API_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# public/pdf.worker.min.mjs is generated (git-ignored), so a fresh checkout has no public/ folder: make it here.
RUN node scripts/copy-pdf-worker.mjs && npm run build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3100 HOSTNAME=0.0.0.0
RUN addgroup -S provenant && adduser -S provenant -G provenant
COPY --from=build --chown=provenant:provenant /app/.next/standalone ./
COPY --from=build --chown=provenant:provenant /app/.next/static ./.next/static
COPY --from=build --chown=provenant:provenant /app/public ./public
USER provenant
EXPOSE 3100
CMD ["node", "server.js"]
