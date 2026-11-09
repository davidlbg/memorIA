FROM node:24-bookworm-slim
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY apps/extension/package.json ./apps/extension/package.json
COPY packages/shared-types ./packages/shared-types
COPY services/whatsapp-connector ./services/whatsapp-connector
RUN npm ci --workspace services/whatsapp-connector --ignore-scripts
RUN chown -R node:node /workspace
USER node
CMD ["npm","test","--workspace","services/whatsapp-connector","--","tests/acceptance.integration.test.ts"]
