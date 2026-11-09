FROM node:24-bookworm-slim AS build
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY apps/extension/package.json ./apps/extension/package.json
COPY packages/shared-types/package.json ./packages/shared-types/package.json
COPY services/whatsapp-connector/package.json ./services/whatsapp-connector/package.json
RUN npm ci --workspace services/whatsapp-connector --ignore-scripts --no-audit --no-fund
COPY packages/shared-types ./packages/shared-types
COPY services/whatsapp-connector ./services/whatsapp-connector
RUN npm run build --workspace services/whatsapp-connector \
    && npm ci --offline --omit=dev --workspace services/whatsapp-connector --ignore-scripts --no-audit --no-fund \
    && mkdir -p services/whatsapp-connector/node_modules

FROM node:24-bookworm-slim
ENV NODE_ENV=production
WORKDIR /workspace
COPY --from=build /workspace/node_modules ./node_modules
COPY --from=build /workspace/packages/shared-types ./packages/shared-types
COPY --from=build /workspace/services/whatsapp-connector/package.json ./services/whatsapp-connector/package.json
COPY --from=build /workspace/services/whatsapp-connector/node_modules ./services/whatsapp-connector/node_modules
COPY --from=build /workspace/services/whatsapp-connector/dist ./services/whatsapp-connector/dist
RUN mkdir /data && chown node:node /data
USER node
CMD ["node","services/whatsapp-connector/dist/index.js"]
