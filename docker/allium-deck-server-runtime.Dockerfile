FROM gcr.io/distroless/cc-debian13

COPY allium-deck-server /allium-deck-server

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/allium-deck-server"]
CMD ["--bind", "0.0.0.0:8080"]
