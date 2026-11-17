# NOT VERIFIED LOCALLY: Docker is not installed on the build machine.
FROM python:3.14-slim

# Set WITH_MSSQL=true to add the Microsoft ODBC driver and pyodbc for SQL Server.
ARG WITH_MSSQL=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN if [ "$WITH_MSSQL" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends curl gnupg unixodbc \
      && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
         | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
      && echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
         > /etc/apt/sources.list.d/mssql.list \
      && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && if [ "$WITH_MSSQL" = "true" ]; then pip install pyodbc; fi

COPY app ./app
COPY data ./data

RUN useradd --create-home appuser && mkdir -p /srv/var && chown appuser /srv/var
USER appuser

EXPOSE 4102
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "4102"]
