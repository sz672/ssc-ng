{smcl}
{* *! version 1.0.0 27sep2026}{...}
{title:Title}

{p 4 4 2}
{cmd:sscng_example} {hline 2} Double a numeric value in the SSC-NG local pilot.

{title:Syntax}

{p 8 12 2}
{cmd:sscng_example,} {opt value(#)}

{title:Description}

{p 4 4 2}
Version 1.0.0 returns twice the supplied value. This is an arithmetic test
fixture for the local submission and review workflow, not a research method.
It has no package dependencies and requires Stata 16 or later.

{title:Example}

{p 8 12 2}{cmd:. sscng_example, value(2)}
{p 8 12 2}{cmd:. display r(result)}
{p 8 12 2}4

{title:Stored results}

{p 4 4 2}{cmd:r(result)} contains twice the supplied value.
