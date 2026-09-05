# One application repository and release

The personal system is maintained and deployed together, so data, research,
risk, simulation, broker execution and its browser view share one repository and
Python package.
This removes internal HTTP and copied protocol models while retaining ownership
through ordinary module interfaces. Separate deployment is deferred until
operational isolation or measured load earns its cost; legacy runtime paths are
removed rather than bridged during this one-way change.

The same application owns durable source files and large analytical products;
PostgreSQL owns their catalog, provenance and operational facts. This keeps file
volume out of transactional records without adding a storage deployment. The
trade-off is explicit publication and database-plus-file recovery, specified in
[the lifecycle design](../ARCHITECTURE.md#8-持久化界面与运行维护).
