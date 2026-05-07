Add a checkout webhook flow that confirms orders against the payments
provider after settlement. The handler must record outcomes via the
repository layer and ship with unit tests covering retry-on-timeout.
