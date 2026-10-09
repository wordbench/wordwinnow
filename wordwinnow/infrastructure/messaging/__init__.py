"""
Kafka as the transport between the services.

Domain events leave the process as versioned JSON messages, one topic per
message version, and come back as bytes that the consumer loop hands to a
handler together with the trace context they traveled with.
"""
