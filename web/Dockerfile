FROM node:20-slim AS build
WORKDIR /web
ARG NEXT_PUBLIC_API_BASE=http://localhost:8080
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx next build

FROM node:20-slim
WORKDIR /web
RUN npm i -g serve@14
COPY --from=build /web/out ./out
EXPOSE 3000
CMD ["serve", "out", "-l", "3000"]
