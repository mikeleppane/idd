Feature: Feature flag kill-switch
  Scenario: Disabled flag blocks payments
    Given the flag enable_payments is off
    When a payment is requested
    Then the response is HTTP 503
