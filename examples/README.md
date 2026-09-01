# Protocol examples

These examples are small, sanitized documents for local validation and schema
exploration. They are not live tasks and must not be pasted into the production
dispatcher without updating identity, head SHA, scope, owner, and acceptance
criteria for the target project.

~~~sh
for file in examples/*.json; do
  aictrl validate "$file"
done
~~~

The examples use the repository's current historical baseline SHA so they remain
deterministic fixtures. A real dispatch must bind to the current target head.
