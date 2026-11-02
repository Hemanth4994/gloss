| run | id | kind | outcome | tagged | explanation |
|---|---|---|---|---|---|
| dev_definitions | j01 | join | wrong_answer | hand | fan-out: order_items joined straight to orders, so the order-level discount was subtracted once per item row; Delhi instead of Bengaluru. |
| dev_definitions | j08 | join | wrong_answer | hand | wrong aggregation level: HAVING two orders applied to each city group, not to each customer, so customers with one completed order were counted. |
| dev_definitions_run2 | d10 | definition | wrong_answer | hand | wrong aggregation level: MAX(discount) across a customer's orders instead of their sum, and refunds joined to item rows; values overstated, same five customers. |
| dev_definitions_run2 | j01 | join | wrong_answer | hand | fan-out: Same error as the first dev run: SUM(discount) over joined item rows. |
| dev_none | d01 | definition | wrong_answer | none | not yet tagged |
| dev_none | d02 | definition | wrong_answer | none | not yet tagged |
| dev_none | d03 | definition | false_refusal | auto | refused: The database does not contain a definition or tracking mechanism for 'active' customers (e.g. definition of what makes a customer active within a time window). |
| dev_none | d04 | definition | wrong_answer | none | not yet tagged |
| dev_none | d05 | definition | wrong_answer | auto | equals the gold query with these traps ignored: status |
| dev_none | d06 | definition | wrong_answer | none | not yet tagged |
| dev_none | d07 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test+tz |
| dev_none | d08 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test |
| dev_none | d09 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test+status |
| dev_none | d10 | definition | wrong_answer | none | not yet tagged |
| dev_none | d11 | definition | wrong_answer | none | not yet tagged |
| dev_none | d12 | definition | false_refusal | auto | refused: The database contains refunds, but return rate could be defined either by order count or item count, and the specific definition of return rate is not provided. |
| dev_none | j01 | join | wrong_answer | none | not yet tagged |
| dev_none | j02 | join | wrong_answer | auto | equals the gold query with these traps ignored: test |
| dev_none | j03 | join | wrong_answer | auto | equals the gold query with these traps ignored: test+tz |
| dev_none | j04 | join | wrong_answer | none | not yet tagged |
| dev_none | j05 | join | false_refusal | auto | refused: The orders table does not contain a 'cancelled' status or any field indicating order cancellation. |
| dev_none | j06 | join | wrong_answer | auto | equals the gold query with these traps ignored: test |
| dev_none | j07 | join | wrong_answer | auto | equals the gold query with these traps ignored: test |
| dev_none | j08 | join | wrong_answer | none | not yet tagged |
| dev_tables | d01 | definition | wrong_answer | none | not yet tagged |
| dev_tables | d02 | definition | false_refusal | auto | refused: Average order value (AOV) is not defined. We do not have a standard definition for whether it should be based on completed orders only, gross order value before |
| dev_tables | d03 | definition | false_refusal | auto | refused: The database contains customer signups and order history, but there is no definition, column, or tracking mechanism for 'active customers today'. |
| dev_tables | d04 | definition | wrong_answer | none | not yet tagged |
| dev_tables | d05 | definition | wrong_answer | auto | equals the gold query with these traps ignored: status |
| dev_tables | d06 | definition | false_refusal | auto | refused: The term GMV (Gross Merchandise Value) is not defined in the schema or definitions. Furthermore, there is no definition for last month's timeframe relative to t |
| dev_tables | d07 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test+tz |
| dev_tables | d08 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test |
| dev_tables | d09 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test+status |
| dev_tables | d10 | definition | false_refusal | auto | refused: Lifetime value is not defined in the schema or definitions. There is no standard formula provided for lifetime value (e.g., total completed order amounts minus  |
| dev_tables | d11 | definition | false_refusal | auto | refused: The schema does not provide a definition or formula for net revenue, nor is there a standard definition of net revenue among the provided definitions. |
| dev_tables | d12 | definition | wrong_answer | none | not yet tagged |
| dev_tables | j01 | join | false_refusal | auto | refused: Net revenue is not defined in the schema or definitions provided, and the exact formula involving orders, order_items, discounts, and refunds is missing. |
| dev_tables | j02 | join | false_refusal | auto | refused: GMV is not defined in the provided definitions. |
| dev_tables | j03 | join | wrong_answer | auto | equals the gold query with these traps ignored: test+tz |
| dev_tables | j04 | join | wrong_answer | none | not yet tagged |
| dev_tables | j05 | join | wrong_answer | auto | equals the gold query with these traps ignored: test+status |
| dev_tables | j06 | join | wrong_answer | auto | equals the gold query with these traps ignored: test |
| dev_tables | j07 | join | wrong_answer | auto | equals the gold query with these traps ignored: test |
| dev_tables | j08 | join | wrong_answer | none | not yet tagged |
| test_definitions | t01 | definition | wrong_answer | hand | fan-out: The order-level discount and refund were subtracted once per item row. The IST dates, test filter and status rule were all applied correctly. |
| test_definitions | t11 | definition | wrong_answer | hand | wrong aggregation level: MAX(discount) and MAX(refund) across a customer's orders instead of their sum, so lifetime value was overstated and 216 customers passed 15000 instead of 210. |
| test_definitions | j12 | join | wrong_answer | hand | fan-out: The discount was subtracted once per item row (the refund was aggregated to the order first, correctly). Rules were otherwise applied correctly. |
| test_none | t01 | definition | wrong_answer | hand | fan-out: Discount and refund repeated per item row; also no completed-only rule, no test exclusion, UTC dates. |
| test_none | t02 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test |
| test_none | t03 | definition | wrong_answer | hand | clock instead of as-of date: 30 days back from the latest order timestamp in UTC. |
| test_none | t04 | definition | wrong_answer | hand | new customer from signup: Signups in the previous calendar month by the system clock; returned 0. |
| test_none | t05 | definition | wrong_answer | hand | returns from refunds: Return rate computed as distinct refunded orders over orders, with the latest-timestamp window. |
| test_none | t06 | definition | wrong_answer | hand | clock instead of as-of date: Current month by the system clock; no rows, so NULL. |
| test_none | t07 | definition | wrong_answer | auto | equals the gold query with these traps ignored: tz |
| test_none | t08 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test+tz |
| test_none | t09 | definition | wrong_answer | hand | clock instead of as-of date: NOW() minus 60 days. |
| test_none | t10 | definition | wrong_answer | hand | metric defined differently: All non-cancelled orders, refunds subtracted, discount ignored; lifetime value counts completed orders only and subtracts discounts. |
| test_none | t11 | definition | wrong_answer | hand | metric defined differently: All non-cancelled orders, no refunds, test accounts included, and the discount repeated per item row. |
| test_none | t12 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test |
| test_none | j09 | join | wrong_answer | hand | new customer from signup: Customers per channel, test accounts included. |
| test_none | j10 | join | wrong_answer | auto | equals the gold query with these traps ignored: test+status |
| test_none | j11 | join | wrong_answer | auto | equals the gold query with these traps ignored: test+status |
| test_none | j12 | join | wrong_answer | hand | fan-out: Discount and refund repeated per item row; no test exclusion, no completed-only rule, UTC dates. |
| test_none | j13 | join | wrong_answer | hand | metric defined differently: Difference of IST timestamps in fractional days instead of calendar dates, no test exclusion. |
| test_none | j14 | join | wrong_answer | hand | metric defined differently: Spend over non-cancelled orders with no refunds or discounts, test accounts included. |
| test_none | j15 | join | wrong_answer | auto | equals the gold query with these traps ignored: test |
| test_none | j16 | join | wrong_answer | auto | equals the gold query with these traps ignored: test+tz |
| test_tables | t01 | definition | false_refusal | auto | refused: There is no definition for net revenue provided in the prompt's definitions or schema. |
| test_tables | t02 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test |
| test_tables | t03 | definition | wrong_answer | hand | clock instead of as-of date: current_timestamp minus 30 days; the real date is after the end of the data, so the answer was 0. |
| test_tables | t04 | definition | wrong_answer | hand | new customer from signup: Counted signups in the previous calendar month by the system clock; returned 0. |
| test_tables | t05 | definition | wrong_answer | hand | clock instead of as-of date: current_timestamp minus 90 days over all statuses. |
| test_tables | t06 | definition | wrong_answer | hand | clock instead of as-of date: date_trunc of the current month by the system clock; no rows, so NULL. |
| test_tables | t07 | definition | wrong_answer | auto | equals the gold query with these traps ignored: tz |
| test_tables | t08 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test+tz |
| test_tables | t09 | definition | wrong_answer | hand | clock instead of as-of date: NOW() minus 60 days. |
| test_tables | t11 | definition | false_refusal | auto | refused: Lifetime value is not defined in the schema or definitions. The data contains orders, items, and refunds, but lifetime value could be calculated in multiple way |
| test_tables | t12 | definition | wrong_answer | auto | equals the gold query with these traps ignored: test |
| test_tables | j09 | join | wrong_answer | hand | new customer from signup: Counted customers per channel (test accounts excluded) instead of customers by first completed order. |
| test_tables | j10 | join | wrong_answer | auto | equals the gold query with these traps ignored: test+status |
| test_tables | j11 | join | wrong_answer | hand | metric defined differently: GMV taken after discounts and only for completed and returned orders; GMV is before discounts and excludes only cancelled orders. |
| test_tables | j12 | join | wrong_answer | hand | fan-out: Discount and refund repeated per item row; also no test exclusion, no completed-only rule and UTC dates. |
| test_tables | j13 | join | wrong_answer | auto | equals the gold query with these traps ignored: test |
| test_tables | j14 | join | false_refusal | auto | refused: The lifetime value (LTV) metric is not defined in the schema or definitions provided, so it is unclear how to calculate a customer's lifetime value. |
| test_tables | j15 | join | wrong_answer | auto | equals the gold query with these traps ignored: test |
| test_tables | j16 | join | wrong_answer | auto | equals the gold query with these traps ignored: test+tz |
