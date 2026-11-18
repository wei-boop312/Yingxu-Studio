FROM node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c AS build
WORKDIR /app
ENV CI=true
RUN npm install --global pnpm@11.25.0
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web/ ./
RUN pnpm build

FROM nginx:stable-alpine@sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311 AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Cross Studio web" org.opencontainers.image.revision=$VCS_REF
COPY --from=build /app/dist /usr/share/nginx/html
COPY ops/nginx.conf /etc/nginx/nginx.conf
COPY third_party /usr/share/nginx/html/third_party
USER 101:101
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g","daemon off;"]
