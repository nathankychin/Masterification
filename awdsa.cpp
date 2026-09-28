#include <algorithm>
#include <cmath>
#include <cctype>
#include <iostream>
#include <string>
#include <vector>

struct Skill {
    std::string name;
    std::string context;
    double importance;
    double usageFrequency;
    double readiness;
    int daysSincePractice;
};

double clamp(double value, double low, double high) {
    return std::max(low, std::min(high, value));
}

std::string toLowerCopy(std::string input) {
    std::transform(input.begin(), input.end(), input.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return input;
}

double calculateRiskWeight(const Skill& skill) {
    double importanceFactor = skill.importance / 10.0;
    double usageFactor = (10.0 - skill.usageFrequency) / 10.0;
    return 1.0 + (importanceFactor * 0.8) + (usageFactor * 0.7);
}

double projectedReadiness(const Skill& skill, int daysSinceLastPractice) {
    double risk = calculateRiskWeight(skill);
    double decayRate = 0.05 * risk;
    double projected = skill.readiness * std::exp(-decayRate * daysSinceLastPractice);
    return clamp(projected, 0.0, 100.0);
}

std::string generateScenario(const Skill& skill) {
    std::string lower = toLowerCopy(skill.name);

    if (lower.find("cpr") != std::string::npos) {
        return "Scenario: A patient in bay 3 just became unresponsive. Walk through your first 60 seconds and explain the actions you would take.";
    }

    if (lower.find("sql") != std::string::npos) {
        return "Scenario: A quarterly report needs a customer-order join. Describe the query you would write and why it would work.";
    }

    if (lower.find("spanish") != std::string::npos || lower.find("language") != std::string::npos) {
        return "Scenario: You are meeting your host family again after months away. Hold a short, natural conversation in the target language.";
    }

    if (lower.find("safety") != std::string::npos || lower.find("lockout") != std::string::npos) {
        return "Scenario: A machine has just entered a hazardous state. Describe the first steps you would take to stabilize the situation safely.";
    }

    return "Scenario: You need to use this skill in a realistic high-pressure situation. Give a short response that shows the correct first steps and key decision points.";
}

std::vector<std::string> expectedKeywords(const Skill& skill) {
    std::string lower = toLowerCopy(skill.name);

    if (lower.find("cpr") != std::string::npos) {
        return {"check", "call", "compressions", "airway", "breathing"};
    }

    if (lower.find("sql") != std::string::npos) {
        return {"join", "select", "where", "group", "from"};
    }

    if (lower.find("spanish") != std::string::npos || lower.find("language") != std::string::npos) {
        return {"hola", "gracias", "como", "estoy", "puedo"};
    }

    if (lower.find("safety") != std::string::npos || lower.find("lockout") != std::string::npos) {
        return {"isolate", "verify", "lock", "safe", "hazard"};
    }

    return {"first", "steps", "verify", "decision"};
}

int evaluateResponse(const Skill& skill, const std::string& response) {
    std::string lowered = toLowerCopy(response);
    int score = 0;

    for (const std::string& keyword : expectedKeywords(skill)) {
        if (lowered.find(keyword) != std::string::npos) {
            ++score;
        }
    }

    if (lowered.find("first") != std::string::npos && lowered.find("steps") != std::string::npos) {
        score += 1;
    }

    if (lowered.find("because") != std::string::npos || lowered.find("why") != std::string::npos) {
        score += 1;
    }

    return clamp(score * 20.0, 0.0, 100.0);
}

std::string feedbackFor(const Skill& skill, int score) {
    if (score >= 80) {
        return "Strong readiness. The core actions were clear and timely.";
    }

    if (score >= 50) {
        return "Moderate readiness. The response captured the main idea, but some key steps were missing.";
    }

    return "Low readiness. The response was too vague and would likely slow down recall under pressure.";
}

void printStatus(const std::vector<Skill>& skills) {
    std::cout << "\nCurrent skill readiness:\n";
    for (size_t i = 0; i < skills.size(); ++i) {
        const Skill& skill = skills[i];
        double current = projectedReadiness(skill, skill.daysSincePractice);
        std::cout << i + 1 << ". " << skill.name << "\n";
        std::cout << "   Context: " << skill.context << "\n";
        std::cout << "   Readiness: " << static_cast<int>(current) << "%\n";
        std::cout << "   Risk weighting: " << calculateRiskWeight(skill) << "x\n";
    }
}

int main() {
    std::vector<Skill> skills = {
        {"CPR", "Nursing student emergency response", 9.5, 3.0, 82.0, 90},
        {"SQL joins", "Quarterly reporting workflow", 7.5, 4.0, 74.0, 45},
        {"Spanish conversation", "Study-abroad fluency maintenance", 6.5, 2.5, 68.0, 120},
        {"Safety lockout procedure", "Engineering certification compliance", 9.0, 4.0, 77.0, 180}
    };

    std::cout << "Skill Atrophy Prevention Tracker\n";
    std::cout << "Prototype for preserving high-stakes, infrequently used skills.\n";

    while (true) {
        printStatus(skills);
        std::cout << "\nCommands: practice <number> | status | quit\n";
        std::string command;
        std::getline(std::cin, command);

        if (command == "quit") {
            break;
        }

        if (command == "status") {
            continue;
        }

        if (command.rfind("practice ", 0) == 0) {
            int index = 0;
            try {
                index = std::stoi(command.substr(9)) - 1;
            } catch (const std::exception&) {
                std::cout << "Please enter a valid number.\n";
                continue;
            }

            if (index < 0 || static_cast<size_t>(index) >= skills.size()) {
                std::cout << "That skill does not exist.\n";
                continue;
            }

            Skill& selected = skills[index];
            std::cout << "\n" << generateScenario(selected) << "\n";
            std::cout << "Enter your response: ";
            std::string response;
            std::getline(std::cin, response);

            int score = evaluateResponse(selected, response);
            double before = projectedReadiness(selected, selected.daysSincePractice);
            double after = (before * 0.6) + (score * 0.4);
            selected.readiness = clamp(after, 0.0, 100.0);
            selected.daysSincePractice = 0;

            std::cout << "Readiness before practice: " << static_cast<int>(before) << "%\n";
            std::cout << "Readiness after practice: " << static_cast<int>(selected.readiness) << "%\n";
            std::cout << "Assessment: " << feedbackFor(selected, score) << "\n";
        } else {
            std::cout << "Unknown command.\n";
        }
    }

    std::cout << "\nSession complete.\n";
    return 0;
}


