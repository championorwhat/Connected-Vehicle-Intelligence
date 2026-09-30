Feature: Keep the fleet running
  Fleet managers act on predicted failures before they strand a vehicle, and see only
  their own fleet. These scenarios run the real API and planner against PostgreSQL and
  Redis (tests/integration/test_acceptance.py).

  Background:
    Given two fleet customers, "acme" and "rival", each with a fleet manager
    And acme has a technician and an analyst

  Scenario: A critical alert becomes a scheduled repair
    Given acme's vehicle reports a critical coolant overheat
    When the planner runs
    Then acme sees a proposed work order for that vehicle's cooling system
    When acme's fleet manager schedules it for tomorrow
    Then the work order is scheduled for tomorrow
    And the audit trail records who proposed and who scheduled it

  Scenario: Planning twice never books the same repair twice
    Given acme's vehicle reports a critical coolant overheat
    When the planner runs
    And the planner runs again
    Then acme has exactly one active work order for that vehicle's cooling system

  Scenario: A competitor cannot see another fleet's vehicle
    When rival's fleet manager asks for acme's vehicle
    Then the answer is "not found", as if the vehicle did not exist

  Scenario: A technician can finish repairs but not book them
    Given acme's vehicle reports a critical coolant overheat
    When the planner runs
    And acme's technician tries to schedule the proposed work order
    Then the request is refused as forbidden
    And the refusal is recorded in the audit trail

  Scenario: An analyst sees where vehicles are only to about a kilometre
    Given acme's vehicle is live at 12.971598, 77.594566
    When acme's analyst opens the vehicle
    Then its position is 12.97, 77.59 with precision "approx_1km"
    When acme's fleet manager opens the vehicle
    Then its position is 12.971598, 77.594566 with precision "precise"
