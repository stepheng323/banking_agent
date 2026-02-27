"""Lambda entrypoint for Gateway webhook ingress."""

from mangum import Mangum

from apps.gateway.main import app

handler = Mangum(app)
