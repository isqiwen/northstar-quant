FROM node:22-bookworm-slim AS build
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
ARG APP
RUN npx next build apps/$APP && node assemble.mjs $APP
RUN cp -a apps/$APP/.next/standalone /out

FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /web
ARG APP
ENV NORTHSTAR_FRONTEND_APP=$APP
COPY --from=build --chown=node:node /out ./
USER node
EXPOSE 3000
CMD ["sh", "-c", "exec node apps/$NORTHSTAR_FRONTEND_APP/server.js"]
