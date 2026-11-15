FROM python:3.12-slim

WORKDIR /app

RUN useradd -m -u 1000 user \
    && mkdir -p /home/user/.cache/spherex \
    && chown -R user:user /home/user/.cache
USER user
ENV PATH="/home/user/.local/bin:${PATH}"
ENV PORT=8000
ENV SPHEREX_CACHE_DIR=/home/user/.cache/spherex

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY --chown=user . .

EXPOSE 8000 7860

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
