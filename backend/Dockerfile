FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

RUN pip install --no-cache-dir \
    numpy \
    celery \
    redis \
    rasterio \
    shapely \
    geopandas \
    requests \
    psycopg[binary] \
    pillow \
    matplotlib \
    imageio \
    anthropic \
    flask \
    flask-cors \
    gunicorn \
    streamlit \
    streamlit-folium \
    folium \
    plotly \
    pandas \
    flask-cors

WORKDIR /app
COPY app /app/app

CMD ["celery", "-A", "app.worker", "worker", "--loglevel=info"]

# Add colleague's working dependencies
RUN pip install --no-cache-dir \
    geoai-py \
    streamlit-folium \
    folium \
    planetary-computer \
    pystac-client \
    rioxarray
