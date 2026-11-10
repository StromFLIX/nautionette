# Keep PostgreSQL 17 and its data/entrypoint contract. Rebuild only the privilege
# helper: the upstream gosu binary embeds Go 1.24.6 even in gosu 1.20 releases.
FROM golang:1.27.3-alpine3.24 AS gosu
ADD --checksum=sha256:207a2045e890d3ca3326027c5f585733085960b401a49dbc00e3bb628c054cf8 https://codeload.github.com/tianon/gosu/tar.gz/refs/tags/1.20 /tmp/gosu.tar.gz
WORKDIR /src
RUN tar -xzf /tmp/gosu.tar.gz --strip-components=1 -C /src
# Explicitly lock the two helper modules too; a compiler update does not patch
# source dependencies. Do not fetch arbitrary latest modules at image build.
COPY go.mod go.sum ./
ENV CGO_ENABLED=0
RUN go mod download && go test -mod=readonly ./... \
    && go build -mod=readonly -trimpath -buildvcs=false -o /out/gosu . \
    && go version -m /out/gosu

FROM postgres:17-alpine
RUN apk upgrade --no-cache
COPY --from=gosu /out/gosu /usr/local/bin/gosu
COPY --from=gosu /src/LICENSE /usr/local/share/licenses/gosu/LICENSE
COPY --from=gosu /usr/local/go/LICENSE /usr/local/share/licenses/gosu/Go-LICENSE
COPY --from=gosu /go/pkg/mod/github.com/moby/sys/user@v0.5.0/LICENSE /usr/local/share/licenses/gosu/moby-user-LICENSE
COPY --from=gosu /go/pkg/mod/golang.org/x/sys@v0.50.0/LICENSE /usr/local/share/licenses/gosu/x-sys-LICENSE
COPY NOTICE /usr/local/share/licenses/gosu/NOTICE
RUN gosu --version && gosu nobody true && gosu postgres id
