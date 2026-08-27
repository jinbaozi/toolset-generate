#include <stdexcept>

int main() {
    try {
        throw std::runtime_error("gts-exception");
    } catch (const std::exception &) {
        return 0;
    }
    return 1;
}
